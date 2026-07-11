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
from app.domain.chunking import chunk_normalized_text
from app.domain.claims import (
    ASSIGNMENT_HUMAN,
    ASSIGNMENT_README_REF,
    ASSIGNMENT_REPO_REF,
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    SOURCE_UPLOAD,
    ClaimRepository,
    EvidenceChunk,
    Experience,
    ExperienceStatus,
    StoredEvidence,
    span_ref,
    split_span_ref,
)
from app.domain.project_reconciliation import (
    STATUS_DETECTED,
    ReconciliationResult,
    reconcile_expected_projects,
)
from app.domain.roster import (
    ChunkAssigner,
    RosterProposer,
    SourceDocument,
    detect_entity_overlaps,
)
from app.domain.source_capture import SourceCaptureStore
from app.domain.text_normalization import normalize_source_text
from app.domain.validation_runs import (
    KIND_ENTITY_OVERLAP,
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
from app.services.roster_factory import create_chunk_assigner, create_roster_proposer
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
class RosterAssignmentReport:
    """What one assignment run scoped (per user).

    ``pinned`` counts chunks left untouched because a human assigned them
    (``assignment_method='human'``) — a machine run never overwrites those (H1).
    """

    chunks: int
    assigned: int
    unassigned: int
    pinned: int = 0
    gather: GatherReport = field(default_factory=GatherReport)


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
        """Capture raw, record the ok disposition, and keep the normalized document."""
        if capture_store is not None:
            capture_store.capture(
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


async def run_roster_assignment(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    repository: ClaimRepository,
    settings: Settings | None = None,
    assigner: ChunkAssigner | None = None,
    *,
    capture_store: SourceCaptureStore | None = None,
    validation_log: ValidationRunLog | None = None,
) -> RosterAssignmentReport:
    """Chunk every source with char spans and assign each chunk to a confirmed entity.

    Repo evidence (README chunks + commits) assigns directly to the entity whose
    name/aliases match the repo ref; document chunks go through the assigner. A chunk
    nothing matches stays honestly unassigned and never feeds extraction. Every
    assignment is labeled with HOW it was made, and a chunk a human assigned
    (``assignment_method='human'``) is pinned — this run never overwrites it (H1).
    """
    settings = settings or get_settings()
    assigner = assigner or create_chunk_assigner(settings)
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

    def apply(stored: StoredEvidence, target: int | None, method: str | None) -> None:
        """Assign one chunk unless a human already decided it (the H1 pin)."""
        nonlocal chunks, assigned, pinned
        chunks += 1
        if stored.assignment_method == ASSIGNMENT_HUMAN:
            pinned += 1
            assigned += 1 if stored.experience_id is not None else 0
            return
        repository.assign_evidence(stored.id, target, method=method if target is not None else None)
        assigned += 1 if target is not None else 0

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

        pieces = chunk_normalized_text(document.text)
        if document.source_type == SOURCE_GITHUB_README:
            entity = _repo_entity(roster, document.source_ref)
            targets: list[int | None] = [entity.id if entity else None] * len(pieces)
            method = ASSIGNMENT_README_REF
        else:
            targets = assigner.assign([piece.text for piece in pieces], roster)
            method = assigner.method
        for piece, target in zip(pieces, targets, strict=True):
            stored = repository.upsert_evidence(
                user_id,
                EvidenceChunk(
                    document.source_type,
                    span_ref(document.source_ref, piece.start, piece.end),
                    piece.text,
                ),
            )
            apply(stored, target, method)

    report = RosterAssignmentReport(
        chunks=chunks,
        assigned=assigned,
        unassigned=chunks - assigned,
        pinned=pinned,
        gather=gathered.report,
    )
    logger.info(
        "roster assignment for %s: %d chunk(s), %d assigned, %d honestly unassigned, "
        "%d human-pinned (untouched)",
        user_id,
        report.chunks,
        report.assigned,
        report.unassigned,
        report.pinned,
    )
    return report
