"""Claim extraction service: evidence gathering -> two-pass extraction -> PAR gate.

Flow (per evidence group):

    build groups — ROSTER MODE when the user has confirmed entities with assigned
                   evidence (one group per confirmed entity, its chunks only); else
                   the legacy per-file grouping, loudly logged
        -> extract (two-pass; heuristic default, LLM behind a flag)
        -> PAR-validate every draft
        -> failures bounce to re-extraction ONCE, carrying the specific violations
        -> STRUCTURAL failures after the bounce are DROPPED (logged, never queued):
           unspecific problems, fragment actions, and any claim citing evidence
           outside its own group (the project boundary — cross-project Results are
           unrepresentable in roster mode and rejected everywhere)
        -> one outcome span supports at most ONE claim's Result: later claims keep
           the work, lose the Result (back to missing, flagged for review)
        -> advisory failures land in the review queue flagged (validation_flags)
        -> duplicates of content already queued anywhere for the user are dropped
        -> persist evidence, experiences, claims + claim_evidence links

An extractor that cannot answer at all (:class:`ClaimExtractionError` — e.g. the LLM
call failed) skips its group LOUDLY: the failure is logged and recorded in
``validation_runs``, existing claims for that experience stay untouched, and no other
extractor silently substitutes its output.

Every persisted claim is ``pending_review`` — extraction NEVER produces an approved
claim. Idempotent by construction: evidence and experiences upsert, and
``replace_unreviewed_claims`` refreshes only rows no human has acted on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace

from app.config import Settings, get_settings
from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    Claim,
    ClaimExtractionError,
    ClaimExtractor,
    ClaimField,
    ClaimRepository,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    ResultKind,
    ResultStatus,
    StorableClaim,
    claim_content_fingerprint,
)
from app.domain.par_validation import is_structural, validate_claim
from app.domain.text_normalization import normalize_source_text
from app.domain.validation_runs import (
    KIND_EXTRACTION_FAILURE,
    KIND_PAR_VALIDATION,
    ValidationRunLog,
)
from app.integrations.base import (
    DriveClient,
    DriveResponseError,
    GitHubClient,
    GitHubResponseError,
)
from app.services.source_policy import apply_repo_policy, apply_source_policy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionReport:
    """What one extraction run produced (per user)."""

    claims: list[Claim] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # structurally invalid, never queued
    deduped: list[str] = field(default_factory=list)  # duplicates of already-queued content
    failed_groups: list[str] = field(default_factory=list)  # extraction failed outright

    @property
    def flagged(self) -> list[Claim]:
        return [c for c in self.claims if c.validation_flags]

    @property
    def missing_results(self) -> list[Claim]:
        return [c for c in self.claims if c.result_kind is ResultKind.MISSING]


async def gather_drive_groups(
    client: DriveClient, user_id: str, settings: Settings
) -> list[EvidenceGroup]:
    """One evidence group per policy-approved Drive document (whole doc = one chunk)."""
    candidates = await client.list_candidate_sources(user_id)
    groups: list[EvidenceGroup] = []
    for source in apply_source_policy(candidates, settings):
        try:
            document = await client.read_source(source.source_ref)
        except DriveResponseError as exc:
            logger.warning("skipping Drive source %s: %s", source.title, exc)
            continue
        groups.append(
            EvidenceGroup(
                experience=ExperienceSeed(
                    name=document.title,
                    section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
                ),
                chunks=(
                    EvidenceChunk(
                        source_type=SOURCE_DRIVE,
                        source_ref=document.source_ref,
                        chunk_text=normalize_source_text(document.text),
                    ),
                ),
            )
        )
    return groups


async def gather_github_groups(
    client: GitHubClient, user_id: str, settings: Settings
) -> list[EvidenceGroup]:
    """One evidence group per policy-approved repo: README chunk + one chunk per commit.

    Commits are first-class evidence under the V2 content gate. A repo missing a
    README (or with unreadable commits) contributes whatever evidence it does have;
    a repo with no evidence at all is skipped, never a failed run.
    """
    candidates = await client.list_candidate_repos(user_id)
    groups: list[EvidenceGroup] = []
    for repo in apply_repo_policy(candidates, settings):
        chunks: list[EvidenceChunk] = []
        try:
            document = await client.read_repo(repo.repo_ref)
            chunks.append(
                EvidenceChunk(
                    source_type=SOURCE_GITHUB_README,
                    source_ref=repo.repo_ref,
                    chunk_text=normalize_source_text(document.text),
                )
            )
        except GitHubResponseError as exc:
            logger.warning("no README evidence for %s: %s", repo.repo_ref, exc)
        try:
            for commit in await client.list_commits(repo.repo_ref):
                chunks.append(
                    EvidenceChunk(
                        source_type=SOURCE_GITHUB_COMMIT,
                        # repo@sha: self-contained provenance, resolvable to a commit URL
                        source_ref=f"{repo.repo_ref}@{commit.sha}",
                        chunk_text=normalize_source_text(commit.message),
                    )
                )
        except GitHubResponseError as exc:
            logger.warning("no commit evidence for %s: %s", repo.repo_ref, exc)
        if not chunks:
            logger.warning("skipping GitHub repo %s: no readable evidence", repo.repo_ref)
            continue
        groups.append(
            EvidenceGroup(
                experience=ExperienceSeed(
                    name=repo.name,
                    section=ExperienceSection.PROJECTS_HACKATHONS,
                    subtitle=repo.description,
                ),
                chunks=tuple(chunks),
            )
        )
    return groups


def _to_storable(draft: DraftClaim, violations: list[str]) -> StorableClaim:
    """Post-validation persistence shape.

    Passing claims with an evidenced Result are ``verified``; a missing Result (or a
    flagged claim) stays ``unverified`` until a human resolves it in review.
    """
    if violations:
        return StorableClaim(
            draft=draft,
            status=ClaimStatus.PENDING_REVIEW,
            result_status=ResultStatus.UNVERIFIED,
            validation_flags=tuple(violations),
        )
    return StorableClaim(
        draft=draft,
        status=ClaimStatus.PENDING_REVIEW,
        result_status=(
            ResultStatus.UNVERIFIED
            if draft.result_kind is ResultKind.MISSING
            else ResultStatus.VERIFIED
        ),
    )


@dataclass(frozen=True)
class GroupExtraction:
    """One group's gate output: what may be queued, and what was dropped (with why)."""

    storables: list[StorableClaim] = field(default_factory=list)
    dropped: list[tuple[DraftClaim, tuple[str, ...]]] = field(default_factory=list)


# Flag stamped on a claim whose Result was removed because its outcome span already
# supports another claim in the same group (one span, one Result).
DUPLICATE_OUTCOME_FLAG = (
    "duplicate_outcome_span: this outcome quote already supports another claim's "
    "Result; the Result was removed - attest the real impact or approve action-only"
)


def _outside_group_codes(draft: DraftClaim, group: EvidenceGroup) -> tuple[str, ...]:
    """Structural violations for evidence cited from outside the group's chunks.

    In roster mode a group is one confirmed entity's evidence, so an outside citation
    IS a project-boundary violation — a Result imported from another project.
    """
    group_keys = {(chunk.source_type, chunk.source_ref) for chunk in group.chunks}
    codes: list[str] = []
    for ref in draft.evidence:
        if (ref.chunk.source_type, ref.chunk.source_ref) in group_keys:
            continue
        if ref.field is ClaimField.RESULT:
            codes.append(
                "result_project_mismatch: the Result cites evidence outside this "
                f"entity's own chunks ({ref.chunk.source_ref})"
            )
        else:
            codes.append(
                "evidence_outside_project: the claim cites evidence outside this "
                f"entity's own chunks ({ref.chunk.source_ref})"
            )
    return tuple(codes)


def _strip_result(draft: DraftClaim) -> DraftClaim:
    """Remove a draft's Result honestly (back to missing, no links, no metric)."""
    return dc_replace(
        draft,
        result_text=None,
        result_kind=ResultKind.MISSING,
        result_metric_json=None,
        evidence=tuple(ref for ref in draft.evidence if ref.field is not ClaimField.RESULT),
    )


def extract_and_validate_group(extractor: ClaimExtractor, group: EvidenceGroup) -> GroupExtraction:
    """Extract one group's claims, bouncing validator failures to re-extraction once.

    The re-extraction carries the specific violations. After the bounce:

    * claims with STRUCTURAL violations (unspecific Problems, fragment Actions,
      evidence cited from outside the group) are dropped — not reviewable candidates;
    * one outcome span supports at most ONE claim's Result — later claims keep their
      work, lose the Result (missing + flagged);
    * advisory violations land in the queue as flags.

    A failure on the bounce itself falls back to the first pass's drafts (flagged)
    rather than losing them.
    """
    drafts = extractor.extract(group)
    violations_per_draft = [validate_claim(d) for d in drafts]

    all_violations = [str(v) for vs in violations_per_draft for v in vs]
    if all_violations:
        logger.info(
            "PAR validation bounced %d/%d claim(s) for %r; re-extracting once",
            sum(1 for vs in violations_per_draft if vs),
            len(drafts),
            group.experience.name,
        )
        try:
            drafts = extractor.extract(group, violations=all_violations)
            violations_per_draft = [validate_claim(d) for d in drafts]
        except ClaimExtractionError as exc:
            logger.error(
                "re-extraction failed for %r; keeping the first pass's drafts: %s",
                group.experience.name,
                exc,
            )

    result = GroupExtraction()
    seen_outcome_spans: set[tuple[str, str]] = set()
    for draft, violations in zip(drafts, violations_per_draft, strict=True):
        structural = [str(v) for v in violations if is_structural(v)]
        structural.extend(_outside_group_codes(draft, group))
        if structural:
            logger.warning(
                "dropping structurally invalid claim for %r: %s",
                group.experience.name,
                draft.action_text[:80],
            )
            result.dropped.append((draft, tuple(structural)))
            continue

        flags = [str(v) for v in violations]
        result_spans = {
            (ref.chunk.source_ref, " ".join((ref.outcome_quote or "").split()))
            for ref in draft.evidence
            if ref.field is ClaimField.RESULT and (ref.outcome_quote or "").strip()
        }
        if result_spans & seen_outcome_spans:
            draft = _strip_result(draft)
            flags = [str(v) for v in validate_claim(draft)]
            flags.append(DUPLICATE_OUTCOME_FLAG)
            logger.warning(
                "outcome span reused for %r; Result removed from: %s",
                group.experience.name,
                draft.action_text[:80],
            )
        else:
            seen_outcome_spans |= result_spans

        result.storables.append(_to_storable(draft, flags))
    return result


def gather_roster_groups(user_id: str, repository: ClaimRepository) -> list[EvidenceGroup]:
    """One evidence group per CONFIRMED roster entity, from its assigned chunks only.

    This is the project boundary made physical: the extractor can only cite chunks
    that belong to the entity it is extracting for, so a cross-project Result is
    unrepresentable. Entities with no assigned evidence contribute no group.
    """
    groups: list[EvidenceGroup] = []
    for experience in repository.list_experiences(user_id):
        if experience.status is not ExperienceStatus.CONFIRMED:
            continue
        stored = repository.list_assigned_evidence(user_id, experience.id)
        if not stored:
            continue
        groups.append(
            EvidenceGroup(
                experience=ExperienceSeed(
                    name=experience.name,
                    section=experience.section,
                    subtitle=experience.subtitle,
                    dates=experience.dates,
                    kind=experience.kind,
                    aliases=experience.aliases,
                ),
                chunks=tuple(
                    EvidenceChunk(e.source_type, e.source_ref, e.chunk_text) for e in stored
                ),
            )
        )
    return groups


async def run_claim_extraction(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    repository: ClaimRepository,
    settings: Settings | None = None,
    extractor: ClaimExtractor | None = None,
    *,
    validation_log: ValidationRunLog | None = None,
) -> ExtractionReport:
    """End-to-end: build groups, extract, PAR-validate, persist. Idempotent.

    ROSTER MODE when the user has confirmed entities with assigned evidence (run
    roster detection + confirmation + assignment first); otherwise the legacy
    per-file grouping runs, with a loud log — file-shaped boundaries are the audit's
    root cause and survive only until the roster review has happened.

    With a ``validation_log``, every persisted claim's final PAR verdict is recorded
    as a ``validation_runs`` row (pass, or fail with the specific violations).
    """
    settings = settings or get_settings()
    if extractor is None:
        from app.services.extractor_factory import create_claim_extractor

        extractor = create_claim_extractor(settings)

    groups = gather_roster_groups(user_id, repository)
    if groups:
        logger.info(
            "claim extraction for %s: ROSTER MODE — %d confirmed entity group(s)",
            user_id,
            len(groups),
        )
    else:
        logger.warning(
            "claim extraction for %s: no confirmed roster with assigned evidence; "
            "falling back to per-file grouping (experiences will be file-shaped — "
            "run roster detection and confirm the roster to fix the boundaries)",
            user_id,
        )
        groups = [
            *await gather_drive_groups(drive_client, user_id, settings),
            *await gather_github_groups(github_client, user_id, settings),
        ]

    persisted: list[Claim] = []
    dropped: list[str] = []
    deduped: list[str] = []
    failed_groups: list[str] = []
    for group in groups:
        experience = repository.upsert_experience(user_id, group.experience)
        for chunk in group.chunks:
            repository.upsert_evidence(user_id, chunk)
        try:
            extraction = extract_and_validate_group(extractor, group)
        except ClaimExtractionError as exc:
            # Loud, isolated failure: the group is skipped (existing claims stay),
            # recorded, and reported — never silently answered by another extractor.
            logger.error("extraction failed for %r; skipping the group: %s", experience.name, exc)
            failed_groups.append(experience.name)
            if validation_log is not None:
                validation_log.record(
                    user_id,
                    KIND_EXTRACTION_FAILURE,
                    subject_ref=f"experience:{experience.name}",
                    passed=False,
                    detail=(str(exc),),
                )
            continue

        for draft, violations in extraction.dropped:
            dropped.append(f"{experience.name}: {draft.action_text[:80]}")
            if validation_log is not None:
                validation_log.record(
                    user_id,
                    KIND_PAR_VALIDATION,
                    subject_ref=f"dropped:{experience.name}",
                    passed=False,
                    detail=violations,
                )

        # Cross-experience dedupe: content already queued (or decided) anywhere for
        # this user never queues again. The current experience's own unreviewed rows
        # don't count — replace_unreviewed_claims is about to replace them.
        seen = {
            claim_content_fingerprint(c.problem_text, c.action_text, c.result_text)
            for c in repository.list_claims(user_id)
            if not (
                c.experience_id == experience.id
                and c.status in (ClaimStatus.EXTRACTED, ClaimStatus.PENDING_REVIEW)
            )
        }
        unique: list[StorableClaim] = []
        for storable in extraction.storables:
            draft = storable.draft
            fingerprint = claim_content_fingerprint(
                draft.problem_text, draft.action_text, draft.result_text
            )
            if fingerprint in seen:
                logger.info(
                    "dropping duplicate claim for %r (same content queued elsewhere): %s",
                    experience.name,
                    draft.action_text[:80],
                )
                deduped.append(f"{experience.name}: {draft.action_text[:80]}")
                continue
            seen.add(fingerprint)
            unique.append(storable)

        inserted = repository.replace_unreviewed_claims(user_id, experience.id, unique)
        if validation_log is not None:
            for claim in inserted:
                validation_log.record(
                    user_id,
                    KIND_PAR_VALIDATION,
                    subject_ref=f"claim:{claim.id}",
                    passed=not claim.validation_flags,
                    detail=claim.validation_flags,
                )
        persisted.extend(inserted)

    report = ExtractionReport(
        claims=persisted, dropped=dropped, deduped=deduped, failed_groups=failed_groups
    )
    logger.info(
        "claim extraction for %s: %d claim(s) pending review (%d flagged, %d missing results); "
        "%d dropped as structurally invalid, %d dropped as duplicates, %d group(s) failed",
        user_id,
        len(report.claims),
        len(report.flagged),
        len(report.missing_results),
        len(report.dropped),
        len(report.deduped),
        len(report.failed_groups),
    )
    return report
