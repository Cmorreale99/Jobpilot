"""Roster services: detection (propose entities) and assignment (scope the evidence).

The human-in-the-loop order the audit demands (docs/V2_AUDIT.md §8):

    gather normalized sources
        -> run_roster_detection: propose entities        [machine]
        -> confirm / merge / rename / discard            [HUMAN — the roster review]
        -> run_roster_assignment: chunk + assign evidence [machine]
        -> claim extraction per confirmed entity          (services/claim_extraction.py)

Detection is idempotent and decision-preserving: proposals dedupe against the whole
roster by name and alias, an existing entity of any status is returned unchanged, and
a discarded entity is never re-proposed. Assignment is idempotent too — re-running
reassigns the same chunks (evidence upserts on its span ref).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.domain.chunking import chunk_elements, chunk_normalized_text
from app.domain.claims import (
    ASSIGNMENT_HUMAN,
    ASSIGNMENT_README_REF,
    ASSIGNMENT_REPO_REF,
    ASSIGNMENT_SECTION,
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    SOURCE_UPLOAD,
    Claim,
    ClaimRepository,
    ClaimStatus,
    EvidenceChunk,
    Experience,
    ExperienceStatus,
    StoredEvidence,
    span_ref,
    split_span_ref,
)
from app.domain.evidence_lifecycle import plan_evidence_supersession
from app.domain.project_reconciliation import (
    STATUS_DETECTED,
    ReconciliationResult,
    reconcile_expected_projects,
)
from app.domain.roster import (
    ChunkAssigner,
    RosterProposer,
    SectionAssigner,
    SectionContent,
    SourceDocument,
    detect_entity_overlaps,
)
from app.domain.source_capture import (
    INGESTION_FAILED,
    INGESTION_OK,
    CapturedSourceVersion,
    SourceCaptureStore,
    SourceElementInput,
)
from app.domain.source_structure import (
    STRUCTURER_VERSION,
    SourceElement,
    heading_trail,
    structure_commit_message,
    structure_source_text,
    top_level_sections,
    verify_full_coverage,
)
from app.domain.text_normalization import NORMALIZATION_VERSION, normalize_source_text
from app.domain.validation_runs import (
    KIND_ENTITY_OVERLAP,
    KIND_EVIDENCE_RECONCILIATION,
    KIND_PROJECT_RECONCILIATION,
    KIND_SOURCE_GATHER,
    ValidationRunLog,
)
from app.integrations.base import (
    DriveClient,
    DriveResponseError,
    GitHubClient,
    GitHubResponseError,
    UploadsClient,
)
from app.integrations.uploads import create_uploads_client
from app.services.roster_factory import (
    create_chunk_assigner,
    create_roster_proposer,
    create_section_assigner,
)
from app.services.source_policy import (
    drive_exclusion_reason,
    repo_exclusion_reason,
    upload_exclusion_reason,
)

logger = logging.getLogger(__name__)


# Disposition statuses for the gather report (H2): every discovered candidate ends in
# exactly one of these — a source can be excluded or fail to read, but never vanish.
GATHER_OK = "ok"
GATHER_READ_FAILED = "read_failed"
GATHER_POLICY_EXCLUDED = "policy_excluded"


@dataclass(frozen=True)
class SourceDisposition:
    """What happened to one discovered source during a gather pass."""

    source_type: str
    source_ref: str
    title: str
    status: str  # GATHER_OK | GATHER_READ_FAILED | GATHER_POLICY_EXCLUDED
    reason: str | None = None

    def describe(self) -> str:
        suffix = f" ({self.reason})" if self.reason else ""
        return f"{self.status}: {self.source_type}:{self.source_ref}{suffix}"


@dataclass(frozen=True)
class GatherReport:
    """Full disposition accounting for one gather pass (H2): zero silent drops."""

    dispositions: tuple[SourceDisposition, ...] = ()

    def _with_status(self, status: str) -> list[SourceDisposition]:
        return [d for d in self.dispositions if d.status == status]

    @property
    def ok(self) -> list[SourceDisposition]:
        return self._with_status(GATHER_OK)

    @property
    def read_failed(self) -> list[SourceDisposition]:
        return self._with_status(GATHER_READ_FAILED)

    @property
    def policy_excluded(self) -> list[SourceDisposition]:
        return self._with_status(GATHER_POLICY_EXCLUDED)

    def summary(self) -> dict[str, int]:
        return {
            GATHER_OK: len(self.ok),
            GATHER_READ_FAILED: len(self.read_failed),
            GATHER_POLICY_EXCLUDED: len(self.policy_excluded),
        }


@dataclass(frozen=True)
class GatheredSources:
    """The gather pass's output: the documents plus where everything else went."""

    documents: list[SourceDocument]
    report: GatherReport


@dataclass(frozen=True)
class RosterDetectionReport:
    """What one detection run proposed (per user)."""

    documents: int
    proposed: list[Experience] = field(default_factory=list)
    gather: GatherReport = field(default_factory=GatherReport)


@dataclass(frozen=True)
class SectionDecision:
    """One per-section ownership decision of a structure-aware assignment run (H5)."""

    source_ref: str
    path: str | None  # the root heading text; None = preamble/no heading
    experience_id: int | None
    method: str  # how the SECTION decision was made (its chunks are stamped `section`)


@dataclass(frozen=True)
class EvidenceReconciliation:
    """What one assignment run's supersession pass did (H6): visible replacement.

    ``superseded`` rows were active before this run and absent from the fresh chunk
    set — marked inactive (successor linked when determinable), never deleted.
    ``pins_migrated`` counts human pins carried forward to successors (H1 preserved).
    ``reviewed_stale_claims`` counts REVIEWED claims currently citing inactive
    evidence — the human decided on text that no longer exists upstream; surfaced
    loudly, never auto-resolved. ``warnings`` carries every non-clean event.
    """

    new: int = 0
    reactivated: int = 0
    superseded: int = 0
    pins_migrated: int = 0
    reviewed_stale_claims: int = 0
    warnings: tuple[str, ...] = ()

    def summary(self) -> dict[str, int]:
        return {
            "new": self.new,
            "reactivated": self.reactivated,
            "superseded": self.superseded,
            "pins_migrated": self.pins_migrated,
            "reviewed_stale_claims": self.reviewed_stale_claims,
        }


@dataclass(frozen=True)
class RosterAssignmentReport:
    """What one assignment run scoped (per user).

    ``pinned`` counts chunks left untouched because a human assigned them
    (``assignment_method='human'``) — a machine run never overwrites those (H1).
    ``sections`` records every per-section ownership decision of the structure-aware
    path (H5); ``truncated_prompts`` counts texts cut to an LLM prompt budget this
    run — truncation is a reported event, never silent (F12). ``reconciliation`` is
    the H6 lifecycle accounting: stale rows visibly superseded, never orphaned.
    """

    chunks: int
    assigned: int
    unassigned: int
    pinned: int = 0
    gather: GatherReport = field(default_factory=GatherReport)
    sections: tuple[SectionDecision, ...] = ()
    truncated_prompts: int = 0
    reconciliation: EvidenceReconciliation = field(default_factory=EvidenceReconciliation)


@dataclass(frozen=True)
class MergePrompt:
    """Two confirmed entities sharing evidence — resolved by the existing roster merge."""

    experience_a: Experience
    experience_b: Experience
    shared_outcome_quotes: tuple[str, ...]
    shared_chunk_texts: tuple[str, ...]


@dataclass(frozen=True)
class RosterOverlapReport:
    """What the cross-entity overlap pass found (per user)."""

    prompts: list[MergePrompt] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectReconciliationReport:
    """Where each expected project stands (per user, one result per expected entry)."""

    results: list[ReconciliationResult] = field(default_factory=list)

    @property
    def undetected(self) -> list[ReconciliationResult]:
        return [r for r in self.results if r.status != STATUS_DETECTED]


def _record_structure(
    capture_store: SourceCaptureStore,
    source_type: str,
    version: CapturedSourceVersion,
    raw_text: str,
) -> None:
    """Derive + persist a version's element tree (H4), with the coverage invariant.

    Elements are a pure derivation of the immutable raw payload: an unchanged version
    already structured by the current ``STRUCTURER_VERSION`` (and reconciled) is
    skipped; a structurer bump or a previously failed pass re-derives. A coverage
    violation marks the version ``failed`` — loudly — so a tree that lost characters
    can never quietly pose as a complete account of its source.
    """
    if version.structurer_version == STRUCTURER_VERSION and version.ingestion_status == (
        INGESTION_OK
    ):
        return
    elements = (
        structure_commit_message(raw_text)
        if source_type == SOURCE_GITHUB_COMMIT
        else structure_source_text(raw_text)
    )
    violations = verify_full_coverage(raw_text, elements)
    status = INGESTION_FAILED if violations else INGESTION_OK
    if violations:
        logger.warning(
            "structure reconciliation FAILED for source version %d (%d violation(s)): %s",
            version.id,
            len(violations),
            "; ".join(violations[:5]),
        )
    capture_store.record_elements(
        version.id,
        [
            SourceElementInput(
                sequence_index=element.sequence_index,
                element_type=element.element_type,
                raw_start=element.raw_start,
                raw_end=element.raw_end,
                raw_text=element.raw_text,
                normalized_text=normalize_source_text(element.raw_text),
                level=element.level,
                parent_index=element.parent_index,
            )
            for element in elements
        ],
        structurer_version=STRUCTURER_VERSION,
        ingestion_status=status,
    )


async def gather_source_documents(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    settings: Settings | None = None,
    *,
    uploads_client: UploadsClient | None = None,
    capture_store: SourceCaptureStore | None = None,
    validation_log: ValidationRunLog | None = None,
) -> GatheredSources:
    """Every policy-approved source as one normalized document, with full accounting.

    Drive docs, repo READMEs + commits, and — when ``UPLOADS_DIR`` is configured (or a
    client injected) — local uploads, all normalized before anything downstream reads
    them. This is the single evidence-gathering path of V2, hardened (H2):

    * Every discovered candidate ends in exactly one recorded disposition — gathered,
      policy-excluded (with the reason), or read-failed (with the error). A source
      that didn't load is never indistinguishable from an empty one.
    * Read failures are symmetric across Drive/GitHub/uploads: logged, recorded,
      never fatal to the rest of the gather — and never silent.
    * When ``capture_store`` is provided, each document's **as-received** text is
      persisted (idempotent by content hash) BEFORE normalization — the canonical
      raw layer every downstream transform derives from.
    * When ``validation_log`` is provided, the pass lands in ``validation_runs``
      (kind ``source_gather``; pass = zero read failures).
    """
    settings = settings or get_settings()
    documents: list[SourceDocument] = []
    dispositions: list[SourceDisposition] = []

    def gathered(document: SourceDocument, *, extractor: str, raw_text: str) -> None:
        """Capture raw + its element tree, record ok, keep the normalized document."""
        if capture_store is not None:
            version = capture_store.capture(
                user_id,
                source_type=document.source_type,
                source_ref=document.source_ref,
                title=document.title,
                raw_text=raw_text,
                extractor=extractor,
                mime_type=document.mime_type,
                modified_time=document.modified_time,
                size_bytes=document.size_bytes,
            )
            _record_structure(capture_store, document.source_type, version, raw_text)
        documents.append(document)
        dispositions.append(
            SourceDisposition(document.source_type, document.source_ref, document.title, GATHER_OK)
        )

    def failed(source_type: str, source_ref: str, title: str, exc: Exception) -> None:
        logger.warning("read failed for %s %s: %s", source_type, source_ref, exc)
        dispositions.append(
            SourceDisposition(source_type, source_ref, title, GATHER_READ_FAILED, reason=str(exc))
        )

    def excluded(source_type: str, source_ref: str, title: str, reason: str) -> None:
        dispositions.append(
            SourceDisposition(source_type, source_ref, title, GATHER_POLICY_EXCLUDED, reason=reason)
        )

    drive_extractor = f"drive:{type(drive_client).__name__}"
    for source in await drive_client.list_candidate_sources(user_id):
        reason = drive_exclusion_reason(source, settings)
        if reason is not None:
            excluded(SOURCE_DRIVE, source.source_ref, source.title, reason)
            continue
        try:
            document = await drive_client.read_source(source.source_ref)
        except DriveResponseError as exc:
            failed(SOURCE_DRIVE, source.source_ref, source.title, exc)
            continue
        gathered(
            SourceDocument(
                source_type=SOURCE_DRIVE,
                source_ref=document.source_ref,
                title=document.title,
                text=normalize_source_text(document.text),
                mime_type=document.mime_type,
                modified_time=document.modified_time,
                raw_text=document.text,
            ),
            extractor=drive_extractor,
            raw_text=document.text,
        )

    github_extractor = f"github:{type(github_client).__name__}"
    for repo in await github_client.list_candidate_repos(user_id):
        reason = repo_exclusion_reason(repo, settings)
        if reason is not None:
            excluded(SOURCE_GITHUB_README, repo.repo_ref, repo.name, reason)
            continue
        try:
            readme = await github_client.read_repo(repo.repo_ref)
        except GitHubResponseError as exc:
            failed(SOURCE_GITHUB_README, repo.repo_ref, repo.name, exc)
        else:
            gathered(
                SourceDocument(
                    source_type=SOURCE_GITHUB_README,
                    source_ref=repo.repo_ref,
                    title=repo.name,
                    text=normalize_source_text(readme.text),
                    modified_time=repo.pushed_at,
                    raw_text=readme.text,
                ),
                extractor=github_extractor,
                raw_text=readme.text,
            )
        try:
            commits = await github_client.list_commits(repo.repo_ref)
        except GitHubResponseError as exc:
            failed(SOURCE_GITHUB_COMMIT, f"{repo.repo_ref}@*", repo.name, exc)
            continue
        for commit in commits:
            gathered(
                SourceDocument(
                    source_type=SOURCE_GITHUB_COMMIT,
                    source_ref=f"{repo.repo_ref}@{commit.sha}",
                    title=repo.name,
                    text=normalize_source_text(commit.message),
                    modified_time=commit.authored_at,
                    raw_text=commit.message,
                ),
                extractor=github_extractor,
                raw_text=commit.message,
            )

    uploads_client = uploads_client or create_uploads_client(settings)
    if uploads_client is not None:
        uploads_extractor = f"upload:{type(uploads_client).__name__}"
        for candidate in await uploads_client.list_candidate_uploads():
            reason = upload_exclusion_reason(candidate, settings)
            if reason is not None:
                excluded(SOURCE_UPLOAD, candidate.upload_ref, candidate.title, reason)
                continue
            try:
                upload = await uploads_client.read_upload(candidate.upload_ref)
            except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
                failed(SOURCE_UPLOAD, candidate.upload_ref, candidate.title, exc)
                continue
            gathered(
                SourceDocument(
                    source_type=SOURCE_UPLOAD,
                    source_ref=upload.upload_ref,
                    title=upload.title,
                    text=normalize_source_text(upload.text),
                    mime_type=upload.mime_type,
                    modified_time=upload.modified_time,
                    raw_text=upload.text,
                ),
                extractor=uploads_extractor,
                raw_text=upload.text,
            )

    report = GatherReport(dispositions=tuple(dispositions))
    if report.read_failed:
        logger.warning(
            "gather for %s: %d source(s) failed to read — see the gather report",
            user_id,
            len(report.read_failed),
        )
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_SOURCE_GATHER,
            subject_ref="sources",
            passed=not report.read_failed,
            detail=tuple(d.describe() for d in report.dispositions if d.status != GATHER_OK),
        )
    return GatheredSources(documents=documents, report=report)


async def run_roster_detection(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    repository: ClaimRepository,
    settings: Settings | None = None,
    proposer: RosterProposer | None = None,
    *,
    capture_store: SourceCaptureStore | None = None,
    validation_log: ValidationRunLog | None = None,
) -> RosterDetectionReport:
    """Gather sources, propose entities, persist NEW proposals (existing rows win)."""
    settings = settings or get_settings()
    proposer = proposer or create_roster_proposer(settings)
    gathered = await gather_source_documents(
        drive_client,
        github_client,
        user_id,
        settings,
        capture_store=capture_store,
        validation_log=validation_log,
    )
    documents = gathered.documents

    known = {e.id for e in repository.list_experiences(user_id)}
    proposed: list[Experience] = []
    for entity in proposer.propose(documents):
        experience = repository.propose_experience(user_id, entity.to_seed())
        if experience.id not in known and experience.status is ExperienceStatus.PROPOSED:
            proposed.append(experience)  # genuinely new this run
    logger.info(
        "roster detection for %s: %d document(s), %d proposal(s) awaiting review",
        user_id,
        len(documents),
        len(proposed),
    )
    return RosterDetectionReport(
        documents=len(documents), proposed=proposed, gather=gathered.report
    )


def _confirmed_roster(repository: ClaimRepository, user_id: str) -> list[Experience]:
    return [
        e for e in repository.list_experiences(user_id) if e.status is ExperienceStatus.CONFIRMED
    ]


def run_overlap_detection(
    user_id: str,
    repository: ClaimRepository,
    *,
    validation_log: ValidationRunLog | None = None,
) -> RosterOverlapReport:
    """The §3.7 cross-entity dedupe pass: shared evidence becomes merge prompts.

    Runs over the whole confirmed corpus — the one vantage point per-entity synthesis
    structurally lacks. Deterministic (exact normalized text), read-only: each prompt
    is a suggestion the human resolves through the existing roster merge, never an
    automatic merge and never a status. Recorded in ``validation_runs`` (kind
    ``entity_overlap``) so "did anything share evidence?" is answerable per run.
    """
    roster = {e.id: e for e in _confirmed_roster(repository, user_id)}
    evidence: list[StoredEvidence] = []
    for experience_id in roster:
        evidence.extend(repository.list_assigned_evidence(user_id, experience_id))
    claims = [c for c in repository.list_claims(user_id) if c.experience_id in roster]

    prompts = [
        MergePrompt(
            experience_a=roster[overlap.experience_a_id],
            experience_b=roster[overlap.experience_b_id],
            shared_outcome_quotes=overlap.shared_outcome_quotes,
            shared_chunk_texts=overlap.shared_chunk_texts,
        )
        for overlap in detect_entity_overlaps(claims, evidence)
        if overlap.experience_a_id in roster and overlap.experience_b_id in roster
    ]
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_ENTITY_OVERLAP,
            subject_ref="roster",
            passed=not prompts,
            detail=tuple(
                f"{p.experience_a.name} <-> {p.experience_b.name}: "
                f"{len(p.shared_outcome_quotes)} shared outcome quote(s), "
                f"{len(p.shared_chunk_texts)} shared chunk(s)"
                for p in prompts
            ),
        )
    logger.info(
        "overlap detection for %s: %d confirmed entities, %d merge prompt(s)",
        user_id,
        len(roster),
        len(prompts),
    )
    return RosterOverlapReport(prompts=prompts)


async def run_project_reconciliation(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    repository: ClaimRepository,
    expected_projects: Sequence[str],
    settings: Settings | None = None,
    *,
    validation_log: ValidationRunLog | None = None,
) -> ProjectReconciliationReport:
    """Reconcile the user's expected-project inventory against roster + sources.

    Thin wrapper over the pure :func:`reconcile_expected_projects`: the confirmed
    roster answers "detected", the gathered raw source texts answer "present but not
    parsed", and everything else is honestly ``missing_from_resume_or_source_not_loaded``
    — a fact about the sources, never a parsing failure by default. Each pass is
    recorded in ``validation_runs`` (kind ``project_reconciliation``): pass = every
    expected project detected; the detail lines carry each undetected project's
    status + next action.
    """
    settings = settings or get_settings()
    roster = _confirmed_roster(repository, user_id)
    gathered = await gather_source_documents(drive_client, github_client, user_id, settings)
    documents = gathered.documents
    results = reconcile_expected_projects(expected_projects, roster, [d.text for d in documents])
    report = ProjectReconciliationReport(results=results)

    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_PROJECT_RECONCILIATION,
            subject_ref="expected_projects",
            passed=not report.undetected,
            detail=tuple(
                f"{r.expected_project}: {r.status} (next: {r.next_action})"
                for r in report.undetected
            ),
        )
    logger.info(
        "project reconciliation for %s: %d expected, %d detected, %d needing attention",
        user_id,
        len(results),
        sum(1 for r in results if r.status == STATUS_DETECTED),
        len(report.undetected),
    )
    return report


def _repo_entity(roster: list[Experience], repo_ref: str) -> Experience | None:
    """The confirmed entity a repo's evidence belongs to (matched by name/alias)."""
    for entity in roster:
        if entity.matches_name(repo_ref):
            return entity
    return None


@dataclass(frozen=True)
class _PreparedChunk:
    """One chunk ready to persist: its span ref plus H5 structure linkage."""

    source_ref: str
    text: str  # normalized
    element_index: int | None = None
    section_path: str | None = None


def _prepare_element_chunks(
    document: SourceDocument, elements: Sequence[SourceElement]
) -> list[_PreparedChunk]:
    """Element-derived chunks (H5): raw-coordinate span refs, heading trails attached.

    A chunk whose text normalizes to nothing (separator debris) is dropped — it could
    never be cited.
    """
    prepared: list[_PreparedChunk] = []
    for piece in chunk_elements(elements):
        normalized = normalize_source_text(piece.text)
        if not normalized.strip():
            continue
        prepared.append(
            _PreparedChunk(
                source_ref=span_ref(document.source_ref, piece.raw_start, piece.raw_end),
                text=normalized,
                element_index=piece.element_index,
                section_path=heading_trail(elements, piece.element_index),
            )
        )
    return prepared


def _element_ids_for(
    capture_store: SourceCaptureStore | None, user_id: str, document: SourceDocument
) -> dict[int, int]:
    """Stored element ids by sequence index for a document's active version."""
    if capture_store is None:
        return {}
    version = capture_store.get_active_version(user_id, document.source_type, document.source_ref)
    if version is None:
        return {}
    return {e.sequence_index: e.id for e in capture_store.list_elements(version.id)}


async def run_roster_assignment(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    repository: ClaimRepository,
    settings: Settings | None = None,
    assigner: ChunkAssigner | None = None,
    section_assigner: SectionAssigner | None = None,
    *,
    capture_store: SourceCaptureStore | None = None,
    validation_log: ValidationRunLog | None = None,
) -> RosterAssignmentReport:
    """Chunk every source and assign each chunk to a confirmed entity.

    Structure-aware since H5 (``STRUCTURED_ASSIGNMENT``, default on): chunks are cut
    from source elements — never across an element boundary — and ownership is decided
    once per top-level section subtree, inherited by the section's chunks
    (``assignment_method='section'``). The heading is persisted context, not a prompt
    hint: a Result paragraph with zero entity tokens can no longer be guessed onto a
    lexically-similar wrong entity (the Cooper fix). Repo evidence (README chunks +
    commits) assigns directly to the entity matching the repo ref — with structure,
    correct by construction (a README's tree is one repo-owned document). Structureless
    sources keep the per-chunk assigner, which still refuses ties. A chunk nothing
    matches stays honestly unassigned and never feeds extraction; a human-assigned
    chunk (``assignment_method='human'``) is pinned — this run never overwrites it (H1).
    """
    settings = settings or get_settings()
    assigner = assigner or create_chunk_assigner(settings)
    section_assigner = section_assigner or create_section_assigner(settings)
    roster = _confirmed_roster(repository, user_id)
    if not roster:
        logger.warning(
            "roster assignment for %s: no confirmed entities — confirm the roster first", user_id
        )
        return RosterAssignmentReport(chunks=0, assigned=0, unassigned=0)

    gathered = await gather_source_documents(
        drive_client,
        github_client,
        user_id,
        settings,
        capture_store=capture_store,
        validation_log=validation_log,
    )
    documents = gathered.documents
    chunks = 0
    assigned = 0
    pinned = 0
    truncated = 0
    section_decisions: list[SectionDecision] = []
    recon_new = 0
    recon_reactivated = 0
    recon_superseded = 0
    recon_pins = 0
    recon_warnings: list[str] = []

    def apply(stored: StoredEvidence, target: int | None, method: str | None) -> StoredEvidence:
        """Assign one chunk unless a human already decided it (the H1 pin)."""
        nonlocal chunks, assigned, pinned
        chunks += 1
        if stored.assignment_method == ASSIGNMENT_HUMAN:
            pinned += 1
            assigned += 1 if stored.experience_id is not None else 0
            return stored
        updated = repository.assign_evidence(
            stored.id, target, method=method if target is not None else None
        )
        assigned += 1 if target is not None else 0
        return updated

    def persist(
        document: SourceDocument,
        prepared: Sequence[_PreparedChunk],
        targets: Sequence[int | None],
        methods: Sequence[str | None],
        element_ids: dict[int, int],
    ) -> list[StoredEvidence]:
        fresh: list[StoredEvidence] = []
        for chunk, target, method in zip(prepared, targets, methods, strict=True):
            stored = repository.upsert_evidence(
                user_id,
                EvidenceChunk(
                    document.source_type,
                    chunk.source_ref,
                    chunk.text,
                    element_id=(
                        element_ids.get(chunk.element_index)
                        if chunk.element_index is not None
                        else None
                    ),
                    sequence_index=chunk.element_index,
                    section_path=chunk.section_path,
                ),
            )
            fresh.append(apply(stored, target, method))
        return fresh

    def reconcile(
        document: SourceDocument, previous: list[StoredEvidence], fresh: list[StoredEvidence]
    ) -> None:
        """Supersede, never orphan (H6): active rows become exactly the fresh set."""
        nonlocal recon_new, recon_reactivated, recon_superseded, recon_pins
        plan = plan_evidence_supersession(
            previous, fresh, current_normalization_version=NORMALIZATION_VERSION
        )
        for action in plan.supersede:
            repository.supersede_evidence(action.evidence_id, action.successor_id)
        for migration in plan.pin_migrations:
            # The retained human decision (H1) carries forward to the row that now
            # holds the same text - a pin never dies silently with its stale span.
            repository.assign_evidence(
                migration.successor_id, migration.experience_id, method=ASSIGNMENT_HUMAN
            )
        recon_new += len(plan.new_ids)
        recon_reactivated += len(plan.reactivated_ids)
        recon_superseded += len(plan.supersede)
        recon_pins += len(plan.pin_migrations)
        recon_warnings.extend(plan.warnings)
        if plan.supersede:
            logger.info(
                "evidence reconciliation for %s %s: %d row(s) superseded, %d pin(s) migrated",
                document.source_type,
                document.source_ref,
                len(plan.supersede),
                len(plan.pin_migrations),
            )

    for document in documents:
        if document.source_type == SOURCE_GITHUB_COMMIT:
            base_ref, _ = split_span_ref(document.source_ref)
            repo_ref = base_ref.rpartition("@")[0]
            entity = _repo_entity(roster, repo_ref)
            stored = repository.upsert_evidence(
                user_id,
                EvidenceChunk(document.source_type, document.source_ref, document.text),
            )
            apply(stored, entity.id if entity else None, ASSIGNMENT_REPO_REF)
            continue

        # Everything persisted for this document BEFORE this run's upserts - the
        # reconciliation baseline (H6). Commit refs are exempt above: one immutable
        # content-addressed row per sha, the fresh set never changes shape.
        previous = repository.list_evidence_for_base_ref(
            user_id, document.source_type, document.source_ref
        )

        structured = settings.structured_assignment and document.raw_text is not None
        if not structured:
            # The flat-text path — the pre-H5 behavior, kept behind the flag as the
            # one-release rollback lever (and for callers with no raw text).
            pieces = chunk_normalized_text(document.text)
            if document.source_type == SOURCE_GITHUB_README:
                entity = _repo_entity(roster, document.source_ref)
                targets: list[int | None] = [entity.id if entity else None] * len(pieces)
                method = ASSIGNMENT_README_REF
            else:
                targets = assigner.assign([piece.text for piece in pieces], roster)
                method = assigner.method
                truncated += getattr(assigner, "last_truncated", 0)
            fresh: list[StoredEvidence] = []
            for piece, target in zip(pieces, targets, strict=True):
                stored = repository.upsert_evidence(
                    user_id,
                    EvidenceChunk(
                        document.source_type,
                        span_ref(document.source_ref, piece.start, piece.end),
                        piece.text,
                    ),
                )
                fresh.append(apply(stored, target, method))
            reconcile(document, previous, fresh)
            continue

        # --- structure-aware path (H5) ---------------------------------------------
        raw_text = document.raw_text or ""
        elements = structure_source_text(raw_text)
        prepared = _prepare_element_chunks(document, elements)
        element_ids = _element_ids_for(capture_store, user_id, document)

        if document.source_type == SOURCE_GITHUB_README:
            # One repo-owned tree: every section belongs to the repo's entity.
            entity = _repo_entity(roster, document.source_ref)
            target = entity.id if entity else None
            fresh = persist(
                document,
                prepared,
                [target] * len(prepared),
                [ASSIGNMENT_README_REF] * len(prepared),
                element_ids,
            )
            reconcile(document, previous, fresh)
            continue

        sections = top_level_sections(elements)
        has_headings = any(section.heading_index is not None for section in sections)
        if not has_headings:
            # Structureless source: no section context exists — per-chunk assignment
            # survives here (and only here), still refusing ties.
            targets = assigner.assign([chunk.text for chunk in prepared], roster)
            truncated += getattr(assigner, "last_truncated", 0)
            fresh = persist(
                document, prepared, targets, [assigner.method] * len(prepared), element_ids
            )
            reconcile(document, previous, fresh)
            continue

        contents = [
            SectionContent(
                heading=section.path,
                body="\n".join(
                    elements[index].raw_text
                    for index in section.element_indices
                    if index != section.heading_index
                ),
            )
            for section in sections
        ]
        section_targets = section_assigner.assign_sections(contents, roster)
        truncated += getattr(section_assigner, "last_truncated", 0)
        target_by_element: dict[int, int | None] = {}
        for section, section_target in zip(sections, section_targets, strict=True):
            section_decisions.append(
                SectionDecision(
                    source_ref=document.source_ref,
                    path=section.path,
                    experience_id=section_target,
                    method=section_assigner.method,
                )
            )
            for index in section.element_indices:
                target_by_element[index] = section_target
        targets = [
            target_by_element.get(chunk.element_index) if chunk.element_index is not None else None
            for chunk in prepared
        ]
        methods = [ASSIGNMENT_SECTION if target is not None else None for target in targets]
        fresh = persist(document, prepared, targets, methods, element_ids)
        reconcile(document, previous, fresh)

    # Reviewed claims left citing superseded rows: the human decided on text that no
    # longer exists upstream. Surfaced loudly — in the report, the warnings, and the
    # validation run — never auto-resolved (H6).
    stale_reviewed = list_reviewed_claims_on_superseded_evidence(user_id, repository)
    for claim, stale_rows in stale_reviewed:
        recon_warnings.append(
            f"reviewed claim {claim.id} ({claim.status.value}) cites superseded "
            f"evidence {', '.join(str(r.id) for r in stale_rows)} — the decided text "
            "no longer exists upstream; re-review or re-extract"
        )
    reconciliation = EvidenceReconciliation(
        new=recon_new,
        reactivated=recon_reactivated,
        superseded=recon_superseded,
        pins_migrated=recon_pins,
        reviewed_stale_claims=len(stale_reviewed),
        warnings=tuple(recon_warnings),
    )
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_EVIDENCE_RECONCILIATION,
            subject_ref="evidence",
            passed=not stale_reviewed,
            detail=(
                ", ".join(f"{k}={v}" for k, v in reconciliation.summary().items()),
                *reconciliation.warnings,
            ),
        )

    report = RosterAssignmentReport(
        chunks=chunks,
        assigned=assigned,
        unassigned=chunks - assigned,
        pinned=pinned,
        gather=gathered.report,
        sections=tuple(section_decisions),
        truncated_prompts=truncated,
        reconciliation=reconciliation,
    )
    logger.info(
        "roster assignment for %s: %d chunk(s), %d assigned, %d honestly unassigned, "
        "%d human-pinned (untouched), %d section decision(s), %d truncated prompt(s); "
        "reconciliation: %d new, %d reactivated, %d superseded, %d pin(s) migrated, "
        "%d reviewed claim(s) on stale evidence",
        user_id,
        report.chunks,
        report.assigned,
        report.unassigned,
        report.pinned,
        len(report.sections),
        report.truncated_prompts,
        reconciliation.new,
        reconciliation.reactivated,
        reconciliation.superseded,
        reconciliation.pins_migrated,
        reconciliation.reviewed_stale_claims,
    )
    return report


def list_reviewed_claims_on_superseded_evidence(
    user_id: str, repository: ClaimRepository
) -> list[tuple[Claim, list[StoredEvidence]]]:
    """Every REVIEWED claim citing at least one superseded evidence row (H6).

    The human approved or rejected content whose upstream text has since been
    superseded — a decision made on text that no longer exists in the current
    chunking. This is a standing review surface (``GET /roster/superseded-reviewed``),
    recomputed from the lifecycle columns; it is never auto-resolved.
    """
    results: list[tuple[Claim, list[StoredEvidence]]] = []
    cache: dict[int, StoredEvidence | None] = {}
    for claim in repository.list_claims(user_id):
        if claim.status not in (ClaimStatus.APPROVED, ClaimStatus.REJECTED):
            continue
        stale: list[StoredEvidence] = []
        for link in claim.evidence:
            if link.evidence_id not in cache:
                cache[link.evidence_id] = repository.get_evidence(link.evidence_id)
            row = cache[link.evidence_id]
            if row is not None and not row.is_active:
                stale.append(row)
        if stale:
            results.append((claim, stale))
    return results
