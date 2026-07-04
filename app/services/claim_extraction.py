"""Claim extraction service: evidence gathering -> two-pass extraction -> PAR gate.

Flow (per experience-sized evidence group):

    gather evidence (Drive docs, GitHub READMEs + commits, via the existing policies)
        -> extract (two-pass; heuristic default, LLM behind a flag)
        -> PAR-validate every draft
        -> failures bounce to re-extraction ONCE, carrying the specific violations
        -> second failure lands in the review queue flagged (validation_flags)
        -> persist evidence, experiences, claims + claim_evidence links

Every persisted claim is ``pending_review`` — extraction NEVER produces an approved
claim. Idempotent by construction: evidence and experiences upsert, and
``replace_unreviewed_claims`` refreshes only rows no human has acted on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    Claim,
    ClaimExtractor,
    ClaimRepository,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.domain.par_validation import validate_claim
from app.domain.validation_runs import KIND_PAR_VALIDATION, ValidationRunLog
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
                        chunk_text=document.text,
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
                    chunk_text=document.text,
                )
            )
        except GitHubResponseError as exc:
            logger.warning("no README evidence for %s: %s", repo.repo_ref, exc)
        try:
            for commit in await client.list_commits(repo.repo_ref):
                chunks.append(
                    EvidenceChunk(
                        source_type=SOURCE_GITHUB_COMMIT,
                        source_ref=commit.sha,
                        chunk_text=commit.message,
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


def extract_and_validate_group(
    extractor: ClaimExtractor, group: EvidenceGroup
) -> list[StorableClaim]:
    """Extract one group's claims, bouncing validator failures to re-extraction once.

    The re-extraction carries the specific violations; a claim still failing after
    the second pass lands in the review queue flagged with them.
    """
    drafts = extractor.extract(group)
    violations_per_draft = [[str(v) for v in validate_claim(d)] for d in drafts]

    all_violations = [v for vs in violations_per_draft for v in vs]
    if all_violations:
        logger.info(
            "PAR validation bounced %d/%d claim(s) for %r; re-extracting once",
            sum(1 for vs in violations_per_draft if vs),
            len(drafts),
            group.experience.name,
        )
        drafts = extractor.extract(group, violations=all_violations)
        violations_per_draft = [[str(v) for v in validate_claim(d)] for d in drafts]

    return [
        _to_storable(draft, violations)
        for draft, violations in zip(drafts, violations_per_draft, strict=True)
    ]


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
    """End-to-end: gather evidence, extract, PAR-validate, persist. Idempotent.

    With a ``validation_log``, every persisted claim's final PAR verdict is recorded
    as a ``validation_runs`` row (pass, or fail with the specific violations).
    """
    settings = settings or get_settings()
    if extractor is None:
        from app.services.extractor_factory import create_claim_extractor

        extractor = create_claim_extractor(settings)

    groups = [
        *await gather_drive_groups(drive_client, user_id, settings),
        *await gather_github_groups(github_client, user_id, settings),
    ]

    persisted: list[Claim] = []
    for group in groups:
        experience = repository.upsert_experience(user_id, group.experience)
        for chunk in group.chunks:
            repository.upsert_evidence(user_id, chunk)
        storables = extract_and_validate_group(extractor, group)
        inserted = repository.replace_unreviewed_claims(user_id, experience.id, storables)
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

    report = ExtractionReport(claims=persisted)
    logger.info(
        "claim extraction for %s: %d claim(s) pending review (%d flagged, %d missing results)",
        user_id,
        len(report.claims),
        len(report.flagged),
        len(report.missing_results),
    )
    return report
