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

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings
from app.domain.chunking import chunk_elements, chunk_normalized_text
from app.domain.claims import (
    ASSIGNMENT_HUMAN,
    ASSIGNMENT_README_REF,
    ASSIGNMENT_REPO_REF,
    ASSIGNMENT_SECTION,
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_DOC,
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
from app.domain.repo_docs import (
    is_claude_md,
    is_multi_entity_doc,
    is_readme_path,
    is_root_readme,
    repo_doc_admission_reason,
    repo_doc_title,
)
from app.domain.roster import (
    ChunkAssigner,
    RosterDetectionError,
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
    SectionSubtree,
    SourceElement,
    child_sections,
    heading_trail,
    structure_commit_message,
    structure_source_text,
    top_level_sections,
    verify_full_coverage,
)
from app.domain.text_normalization import NORMALIZATION_VERSION, normalize_source_text
from app.domain.validation_runs import (
    KIND_ASSIGNMENT_FINGERPRINT,
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
    GitHubRepo,
    GitHubRepoFile,
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
# ``awaiting_user_decision`` (MASTER CV REPAIR §5.1.2/§22.1) marks enumerated repo files
# whose admission the user has not decided (source code, tests, notebooks): they are in
# the universe and the denominator, deliberately not ingested, never silently dropped.
GATHER_OK = "ok"
GATHER_READ_FAILED = "read_failed"
GATHER_POLICY_EXCLUDED = "policy_excluded"
GATHER_AWAITING_USER_DECISION = "awaiting_user_decision"


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
class RepoDocAccounting:
    """Per-repository document accounting (MASTER CV REPAIR §4.3/§16.1).

    Distinguishes *repository discovered*, *documentation captured*, *history
    captured*, and *ingestion complete* — commit success can never mask a failed
    README or CLAUDE.md read. ``files_enumerated`` is ``None`` when the tree could
    not be enumerated at all (itself a required failure). README flags cover the
    ROOT README only (nested READMEs are documents counted in ``docs_*``);
    CLAUDE.md flags cover every CLAUDE.md in the tree.
    """

    repo_ref: str
    enumeration_failed: bool = False
    files_enumerated: int | None = None
    docs_ingested: int = 0
    docs_failed: int = 0
    not_admitted: int = 0
    commits_captured: int = 0
    commits_failed: bool = False
    readme_present: bool = False
    readme_captured: bool = False
    claude_md_present: bool = False
    claude_md_captured: bool = False

    @property
    def complete(self) -> bool:
        """Repository ingestion complete: tree enumerated, every admitted doc read,
        and every required doc (README/CLAUDE.md when present) captured."""
        return (
            not self.enumeration_failed
            and self.docs_failed == 0
            and (self.readme_captured or not self.readme_present)
            and (self.claude_md_captured or not self.claude_md_present)
        )

    def required_failures(self) -> list[str]:
        """Named required-source failures (§14.1): these block publication."""
        failures: list[str] = []
        if self.enumeration_failed:
            failures.append(f"{self.repo_ref}: repository file enumeration failed")
        if self.readme_present and not self.readme_captured:
            failures.append(f"{self.repo_ref}: README present but not captured")
        if self.claude_md_present and not self.claude_md_captured:
            failures.append(f"{self.repo_ref}: CLAUDE.md present but not captured")
        return failures


@dataclass(frozen=True)
class GatherReport:
    """Full disposition accounting for one gather pass (H2): zero silent drops."""

    dispositions: tuple[SourceDisposition, ...] = ()
    # Per-repo doc accounting (§16.1): the file universe, its dispositions, and the
    # required-doc capture flags. Empty for gathers with no GitHub repos.
    repo_docs: tuple[RepoDocAccounting, ...] = ()

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

    @property
    def awaiting_user_decision(self) -> list[SourceDisposition]:
        return self._with_status(GATHER_AWAITING_USER_DECISION)

    @property
    def required_failures(self) -> list[str]:
        """Every named required-source failure across the gather (§5.1.5/§14.1)."""
        return [f for acc in self.repo_docs for f in acc.required_failures()]

    @property
    def complete(self) -> bool:
        """True only when nothing failed to read and no required doc is missing.

        This is the gate publication consumes: partial output must never present
        itself as complete (§14.1).
        """
        return not self.read_failed and not self.required_failures

    def summary(self) -> dict[str, int]:
        return {
            GATHER_OK: len(self.ok),
            GATHER_READ_FAILED: len(self.read_failed),
            GATHER_POLICY_EXCLUDED: len(self.policy_excluded),
            GATHER_AWAITING_USER_DECISION: len(self.awaiting_user_decision),
        }

    def coverage(self) -> dict[str, Any]:
        """Coverage with honest denominators (MASTER CV REPAIR §4.16/§5.1.6-8/§16.15).

        DISCOVERY (was the configured universe enumerated?) and PROCESSING (how much
        of the admitted universe was captured?) are separate numbers. The denominator
        is always the actual configured universe — a cached or partial subset can
        never present itself as 100%. Missing files are named, never just counted.
        """
        repositories: list[dict[str, Any]] = []
        for acc in self.repo_docs:
            admitted = acc.docs_ingested + acc.docs_failed
            missing = [
                d.source_ref
                for d in self.read_failed
                if d.source_ref == acc.repo_ref
                or d.source_ref.startswith((f"{acc.repo_ref}/", f"{acc.repo_ref}@"))
            ]
            repositories.append(
                {
                    "repo_ref": acc.repo_ref,
                    "discovery_complete": not acc.enumeration_failed,
                    "files_enumerated": acc.files_enumerated,
                    "docs_admitted": admitted,
                    "docs_ingested": acc.docs_ingested,
                    "not_admitted": acc.not_admitted,
                    "commits_captured": acc.commits_captured,
                    "processing_pct": (
                        round(100.0 * acc.docs_ingested / admitted, 1) if admitted else 100.0
                    ),
                    "fully_ingested": acc.complete,
                    "missing_files": missing,
                    "required_failures": acc.required_failures(),
                }
            )
        docs_admitted = sum(r["docs_admitted"] for r in repositories)
        docs_ingested = sum(r["docs_ingested"] for r in repositories)
        return {
            "repositories": repositories,
            "totals": {
                "repositories_discovered": len(self.repo_docs),
                "files_enumerated": sum(acc.files_enumerated or 0 for acc in self.repo_docs),
                "docs_admitted": docs_admitted,
                "docs_ingested": docs_ingested,
                "awaiting_user_decision": len(self.awaiting_user_decision),
                "policy_excluded": len(self.policy_excluded),
                "read_failures": len(self.read_failed),
                "required_failures": len(self.required_failures),
                "sources_gathered": len(self.ok),
            },
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
    # Refs matching MULTIPLE confirmed entities (MASTER CV REPAIR §4.9/§16.7): their
    # evidence stays unresolved (never first-match assigned) awaiting a user decision.
    ambiguous: tuple[str, ...] = ()
    # Documents whose assigner call failed outright (e.g. an LLM error): their chunks
    # persist honestly unassigned; the failure is bounded, loud, and re-runnable.
    assignment_failures: tuple[str, ...] = ()
    # Documents skipped by the §5.8 fingerprint gate: content, roster, rule versions,
    # and assigner all unchanged since their last completed pass — zero re-spend.
    skipped_unchanged: int = 0


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
    repo_accounting: list[RepoDocAccounting] = []

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

        # --- the repository FILE UNIVERSE (MASTER CV REPAIR §4.1/§6.2/§16.1) --------
        # Enumerate the complete tree; every entry ends in exactly one disposition.
        # READMEs (root + nested), CLAUDE.md, and all Markdown are admitted documents;
        # everything else awaits the user's §22.1 admission decision — visible, never
        # silently absent and never silently ingested.
        enumeration_failed = False
        files: list[GitHubRepoFile] = []
        try:
            files = await github_client.list_repo_files(repo.repo_ref)
        except GitHubResponseError as exc:
            enumeration_failed = True
            failed(SOURCE_GITHUB_DOC, f"{repo.repo_ref}/*", repo.name, exc)

        docs_ingested = 0
        docs_failed = 0
        not_admitted = 0
        readme_present = False
        readme_captured = False
        claude_present_n = 0
        claude_captured_n = 0

        async def ingest_doc(
            path: str,
            doc_source_type: str,
            doc_source_ref: str,
            title: str,
            repo: GitHubRepo = repo,  # bound per iteration (B023)
        ) -> bool:
            """Read one admitted repo document; True when captured."""
            nonlocal docs_ingested, docs_failed
            try:
                document = await github_client.read_repo_file(repo.repo_ref, path)
            except GitHubResponseError as exc:
                docs_failed += 1
                failed(doc_source_type, doc_source_ref, title, exc)
                return False
            gathered(
                SourceDocument(
                    source_type=doc_source_type,
                    source_ref=doc_source_ref,
                    title=title,
                    text=normalize_source_text(document.text),
                    modified_time=repo.pushed_at,
                    raw_text=document.text,
                ),
                extractor=github_extractor,
                raw_text=document.text,
            )
            docs_ingested += 1
            return True

        if not enumeration_failed:
            for repo_file in files:
                path = repo_file.path
                if is_root_readme(path):
                    # The root README keeps its legacy identity (github_readme,
                    # repo_ref) so existing evidence rows stay continuous (H6).
                    readme_present = True
                    readme_captured = await ingest_doc(
                        path, SOURCE_GITHUB_README, repo.repo_ref, repo.name
                    )
                    continue
                admission = repo_doc_admission_reason(path)
                doc_ref = f"{repo.repo_ref}/{path}"
                if admission is not None:
                    not_admitted += 1
                    dispositions.append(
                        SourceDisposition(
                            SOURCE_GITHUB_DOC,
                            doc_ref,
                            repo_doc_title(repo.name, path),
                            GATHER_AWAITING_USER_DECISION,
                            reason=admission,
                        )
                    )
                    continue
                if is_claude_md(path):
                    claude_present_n += 1
                    if await ingest_doc(
                        path, SOURCE_GITHUB_DOC, doc_ref, repo_doc_title(repo.name, path)
                    ):
                        claude_captured_n += 1
                    continue
                await ingest_doc(path, SOURCE_GITHUB_DOC, doc_ref, repo_doc_title(repo.name, path))
        else:
            # Tree unavailable: the repo is already incomplete (a required failure),
            # but still attempt the root README so behavior never regresses below
            # the legacy single-README read.
            try:
                readme = await github_client.read_repo(repo.repo_ref)
            except GitHubResponseError as exc:
                failed(SOURCE_GITHUB_README, repo.repo_ref, repo.name, exc)
            else:
                readme_present = True
                readme_captured = True
                docs_ingested += 1
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

        commits_captured = 0
        commits_failed = False
        try:
            commits = await github_client.list_commits(repo.repo_ref)
        except GitHubResponseError as exc:
            commits_failed = True
            failed(SOURCE_GITHUB_COMMIT, f"{repo.repo_ref}@*", repo.name, exc)
        else:
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
                commits_captured += 1

        repo_accounting.append(
            RepoDocAccounting(
                repo_ref=repo.repo_ref,
                enumeration_failed=enumeration_failed,
                files_enumerated=None if enumeration_failed else len(files),
                docs_ingested=docs_ingested,
                docs_failed=docs_failed,
                not_admitted=not_admitted,
                commits_captured=commits_captured,
                commits_failed=commits_failed,
                readme_present=readme_present,
                readme_captured=readme_captured,
                claude_md_present=claude_present_n > 0,
                claude_md_captured=claude_present_n > 0 and claude_captured_n == claude_present_n,
            )
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

    report = GatherReport(dispositions=tuple(dispositions), repo_docs=tuple(repo_accounting))
    if report.read_failed:
        logger.warning(
            "gather for %s: %d source(s) failed to read — see the gather report",
            user_id,
            len(report.read_failed),
        )
    if report.required_failures:
        logger.warning(
            "gather for %s: %d REQUIRED-source failure(s) — publication must not "
            "proceed on this candidate: %s",
            user_id,
            len(report.required_failures),
            "; ".join(report.required_failures),
        )
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_SOURCE_GATHER,
            subject_ref="sources",
            passed=report.complete,
            detail=(
                *(f"required_failure: {f}" for f in report.required_failures),
                *(
                    d.describe()
                    for d in report.dispositions
                    if d.status in (GATHER_READ_FAILED, GATHER_POLICY_EXCLUDED)
                ),
            ),
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


def _matching_entities(roster: list[Experience], ref: str) -> list[Experience]:
    """EVERY confirmed entity matching ``ref`` by name/alias — never just the first."""
    return [entity for entity in roster if entity.matches_name(ref)]


def _sole_entity(roster: list[Experience], ref: str, ambiguous: list[str]) -> Experience | None:
    """The single matching entity, or ``None`` — a multi-match is recorded ambiguity.

    MASTER CV REPAIR §4.9/§5.2.11/§16.7: canonical assignment may not depend on
    first-match ordering. Multiple matches leave the evidence unresolved (visible in
    the unassigned queue for a user decision) and the ambiguity is reported.
    """
    matches = _matching_entities(roster, ref)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ambiguous.append(
            f"{ref}: matches {len(matches)} confirmed entities "
            f"({', '.join(e.name for e in matches)}) — left unresolved for a user decision"
        )
    return None


def _doc_repo_ref(document: SourceDocument) -> str:
    """The owning repo_ref of a GitHub document (README ref, or the doc ref's prefix)."""
    if document.source_type == SOURCE_GITHUB_README:
        return document.source_ref
    parts = document.source_ref.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else document.source_ref


def _collection_repo_refs(documents: Sequence[SourceDocument]) -> set[str]:
    """Repos evidencing MULTIPLE projects: any repo with nested-README child docs.

    The deterministic collection signal (§3.3/§7.4): a nested README declares a child
    project, so the repository is a container — its root README sections and its
    repo-wide commits must not be force-assigned to any single entity by repo
    reference alone.
    """
    collections: set[str] = set()
    for document in documents:
        if document.source_type != SOURCE_GITHUB_DOC:
            continue
        parts = document.source_ref.split("/")
        if len(parts) >= 3 and is_readme_path("/".join(parts[2:])):
            collections.add("/".join(parts[:2]))
    return collections


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
    skipped_unchanged = 0
    section_decisions: list[SectionDecision] = []
    recon_new = 0
    recon_reactivated = 0
    recon_superseded = 0
    recon_pins = 0
    recon_warnings: list[str] = []

    # The §5.8 assignment cost gate: a document whose content, the roster, the rule
    # versions, AND the assigner generation are all unchanged since its last completed
    # assignment is SKIPPED outright — its rows already reflect exactly this input
    # (deterministic chunking), so re-deciding ownership only re-spends LLM budget.
    # Live 2026-07-13: three interrupted runs re-paid the full assignment pass.
    roster_material = "\x1e".join(
        f"{e.id}\x1f{e.name}\x1f{'|'.join(sorted(e.aliases))}"
        for e in sorted(roster, key=lambda e: e.id)
    )
    assigner_label = (
        f"{type(assigner).__name__}/{type(section_assigner).__name__}/"
        f"structured={settings.structured_assignment}"
    )
    fingerprints_seen: dict[str, str] = {}
    if validation_log is not None:
        for run in validation_log.list_runs(user_id, KIND_ASSIGNMENT_FINGERPRINT):
            if run.detail:
                fingerprints_seen[run.subject_ref] = run.detail[0]  # latest wins

    def document_fingerprint(document: SourceDocument) -> tuple[str, str]:
        material = "\x1e".join(
            (
                document.source_type,
                document.source_ref,
                document.raw_text if document.raw_text is not None else document.text,
                roster_material,
                assigner_label,
                f"norm={NORMALIZATION_VERSION}",
                f"struct={STRUCTURER_VERSION}",
                f"collection={_doc_repo_ref(document) in collections}",
            )
        )
        subject = f"{document.source_type}:{document.source_ref}"
        return subject, hashlib.sha256(material.encode("utf-8")).hexdigest()

    def fingerprint_matches(document: SourceDocument) -> bool:
        if validation_log is None:
            return False
        subject, digest = document_fingerprint(document)
        return fingerprints_seen.get(subject) == digest

    def record_fingerprint(document: SourceDocument) -> None:
        if validation_log is None:
            return
        subject, digest = document_fingerprint(document)
        validation_log.record(
            user_id,
            KIND_ASSIGNMENT_FINGERPRINT,
            subject_ref=subject,
            passed=True,
            detail=(digest,),
        )

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

    # Collection repositories (repos evidencing multiple child projects) and alias
    # ambiguity (MASTER CV REPAIR §4.6-4.9): a container's docs are owned per SECTION,
    # its repo-wide commits stay unresolved/supporting-only, and a ref matching
    # multiple confirmed entities is a recorded ambiguity — never first-match owned.
    collections = _collection_repo_refs(documents)
    ambiguity_notes: dict[str, str] = {}

    def note_ambiguity(ref: str) -> None:
        notes: list[str] = []
        entity = _sole_entity(roster, ref, notes)
        assert entity is None  # callers only note when multiple matched
        if notes:
            ambiguity_notes.setdefault(ref, notes[0])

    def github_doc_boundary(document: SourceDocument) -> tuple[bool, int | None]:
        """Whether this repo document has ONE deterministic user-approved owner.

        ``(True, entity_id)``: the user confirmed exactly one entity carrying this
        doc's ref (a child project's own README) or the repo's ref on a
        single-project repo — the §3.8 deterministic boundary. ``(False, None)``:
        section-level assignment must decide (collection repos, zero matches, or
        ambiguity — the latter recorded).
        """
        if document.source_type == SOURCE_GITHUB_DOC:
            direct = _matching_entities(roster, document.source_ref)
            if len(direct) > 1:
                note_ambiguity(document.source_ref)
                return False, None
            if len(direct) == 1:
                return True, direct[0].id
            if is_multi_entity_doc(document.source_ref):
                # A resume/CV checked into a repo describes MANY entities (§6.1):
                # never repo-boundary-forced — its sections are owned per section.
                return False, None
        repo_ref = _doc_repo_ref(document)
        if repo_ref in collections:
            return False, None
        matches = _matching_entities(roster, repo_ref)
        if len(matches) > 1:
            note_ambiguity(repo_ref)
            return False, None
        if len(matches) == 1:
            return True, matches[0].id
        return False, None

    assignment_failures: list[str] = []

    def note_assignment_failure(document: SourceDocument, exc: Exception) -> None:
        """One assigner failure never kills the run (live 2026-07-13: an LLM call
        with no text blocks crashed the whole assignment pass). The document's
        chunks persist honestly UNASSIGNED — queryable, manually assignable — and
        the failure is loud in the log, the report, and the API response."""
        assignment_failures.append(f"{document.source_type}:{document.source_ref}: {exc}")
        logger.error(
            "assignment failed for %s %s — its chunks stay unassigned this run: %s",
            document.source_type,
            document.source_ref,
            exc,
        )

    for document in documents:
        if document.source_type == SOURCE_GITHUB_COMMIT:
            base_ref, _ = split_span_ref(document.source_ref)
            repo_ref = base_ref.rpartition("@")[0]
            target: int | None = None
            if repo_ref not in collections:
                matches = _matching_entities(roster, repo_ref)
                if len(matches) == 1:
                    target = matches[0].id
                elif len(matches) > 1:
                    note_ambiguity(repo_ref)
            stored = repository.upsert_evidence(
                user_id,
                EvidenceChunk(document.source_type, document.source_ref, document.text),
            )
            apply(stored, target, ASSIGNMENT_REPO_REF)
            continue

        if fingerprint_matches(document):
            # §5.8 skip: content, roster, versions, and assigner unchanged since the
            # last completed pass — the persisted rows already ARE this decision.
            skipped_unchanged += 1
            continue
        failures_before = len(assignment_failures)

        def finish_document(
            document: SourceDocument = document, failures_before: int = failures_before
        ) -> None:
            """Stamp the fingerprint ONLY on a fully successful pass — a document
            whose assigner failed stays eligible for the next run."""
            if len(assignment_failures) == failures_before:
                record_fingerprint(document)

        # Everything persisted for this document BEFORE this run's upserts - the
        # reconciliation baseline (H6). Commit refs are exempt above: one immutable
        # content-addressed row per sha, the fresh set never changes shape.
        previous = repository.list_evidence_for_base_ref(
            user_id, document.source_type, document.source_ref
        )

        is_repo_doc = document.source_type in (SOURCE_GITHUB_README, SOURCE_GITHUB_DOC)
        forced, forced_target = github_doc_boundary(document) if is_repo_doc else (False, None)

        structured = settings.structured_assignment and document.raw_text is not None
        if not structured:
            # The flat-text path — the pre-H5 behavior, kept behind the flag as the
            # one-release rollback lever (and for callers with no raw text).
            pieces = chunk_normalized_text(document.text)
            if forced:
                targets: list[int | None] = [forced_target] * len(pieces)
                method = ASSIGNMENT_README_REF
            else:
                try:
                    targets = assigner.assign([piece.text for piece in pieces], roster)
                    method = assigner.method
                except RosterDetectionError as exc:
                    note_assignment_failure(document, exc)
                    targets = [None] * len(pieces)
                    method = None
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
            finish_document()
            continue

        # --- structure-aware path (H5) ---------------------------------------------
        raw_text = document.raw_text or ""
        elements = structure_source_text(raw_text)
        prepared = _prepare_element_chunks(document, elements)
        element_ids = _element_ids_for(capture_store, user_id, document)

        if forced:
            # A single deterministic user-approved owner (§3.8): a single-project
            # repo's docs, or a child project's own README. Collection-repo root
            # READMEs never take this path — their sections decide (§4.7).
            fresh = persist(
                document,
                prepared,
                [forced_target] * len(prepared),
                [ASSIGNMENT_README_REF] * len(prepared),
                element_ids,
            )
            reconcile(document, previous, fresh)
            finish_document()
            continue

        sections = top_level_sections(elements)
        has_headings = any(section.heading_index is not None for section in sections)
        if not has_headings:
            # Structureless source: no section context exists — per-chunk assignment
            # survives here (and only here), still refusing ties.
            chunk_method: str | None = assigner.method
            try:
                targets = assigner.assign([chunk.text for chunk in prepared], roster)
            except RosterDetectionError as exc:
                note_assignment_failure(document, exc)
                targets = [None] * len(prepared)
                chunk_method = None
            truncated += getattr(assigner, "last_truncated", 0)
            fresh = persist(
                document, prepared, targets, [chunk_method] * len(prepared), element_ids
            )
            reconcile(document, previous, fresh)
            finish_document()
            continue

        target_by_element: dict[int, int | None] = {}

        def decide_sections(
            subtrees: list[SectionSubtree],
            elements: Sequence[SourceElement] = elements,
            document: SourceDocument = document,
            target_by_element: dict[int, int | None] = target_by_element,
        ) -> None:
            """Assign ownership per subtree, descending when a heading decides nothing.

            A subtree WITH child headings is decided by its own heading alone — a
            mixed multi-project body must never vote (MASTER CV REPAIR §4.7). When
            that heading is silent, ownership refines into the child subtrees (a
            portfolio README's title over child-project sections); only a leaf
            subtree may fall back to its body vocabulary.
            """
            nonlocal truncated
            kids_of = [child_sections(elements, subtree) for subtree in subtrees]
            branch = [any(kid.heading_index is not None for kid in kids) for kids in kids_of]
            contents = [
                SectionContent(
                    heading=subtree.path,
                    body=(
                        ""
                        if is_branch
                        else "\n".join(
                            elements[index].raw_text
                            for index in subtree.element_indices
                            if index != subtree.heading_index
                        )
                    ),
                )
                for subtree, is_branch in zip(subtrees, branch, strict=True)
            ]
            section_targets = section_assigner.assign_sections(contents, roster)
            truncated += getattr(section_assigner, "last_truncated", 0)
            for subtree, kids, is_branch, section_target in zip(
                subtrees, kids_of, branch, section_targets, strict=True
            ):
                if section_target is None and is_branch:
                    # Silent heading over child sections: the children decide
                    # themselves; the root heading + its direct prose stay unowned.
                    section_decisions.append(
                        SectionDecision(
                            source_ref=document.source_ref,
                            path=subtree.path,
                            experience_id=None,
                            method=section_assigner.method,
                        )
                    )
                    if subtree.heading_index is not None:
                        target_by_element[subtree.heading_index] = None
                    for kid in kids:
                        if kid.heading_index is None:
                            for index in kid.element_indices:
                                target_by_element[index] = None
                    decide_sections([kid for kid in kids if kid.heading_index is not None])
                    continue
                section_decisions.append(
                    SectionDecision(
                        source_ref=document.source_ref,
                        path=subtree.path,
                        experience_id=section_target,
                        method=section_assigner.method,
                    )
                )
                for index in subtree.element_indices:
                    target_by_element[index] = section_target

        try:
            decide_sections(sections)
        except RosterDetectionError as exc:
            # Ownership undecided: the document's chunks persist honestly unassigned.
            note_assignment_failure(document, exc)
            target_by_element.clear()
        targets = [
            target_by_element.get(chunk.element_index) if chunk.element_index is not None else None
            for chunk in prepared
        ]
        methods = [ASSIGNMENT_SECTION if target is not None else None for target in targets]
        fresh = persist(document, prepared, targets, methods, element_ids)
        reconcile(document, previous, fresh)
        finish_document()

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
        ambiguous=tuple(ambiguity_notes.values()),
        assignment_failures=tuple(assignment_failures),
        skipped_unchanged=skipped_unchanged,
    )
    if report.skipped_unchanged:
        logger.info(
            "roster assignment for %s: %d document(s) skipped — fingerprint unchanged "
            "since their last completed pass (no re-spend)",
            user_id,
            report.skipped_unchanged,
        )
    if report.assignment_failures:
        logger.error(
            "roster assignment for %s: %d document(s) failed assignment and stay unassigned: %s",
            user_id,
            len(report.assignment_failures),
            "; ".join(report.assignment_failures),
        )
    if report.ambiguous:
        logger.warning(
            "roster assignment for %s: %d ambiguous ref(s) left unresolved for a user decision: %s",
            user_id,
            len(report.ambiguous),
            "; ".join(report.ambiguous),
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
