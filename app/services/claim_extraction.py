"""Claim extraction service: roster groups -> two-pass extraction -> PAR gate.

Extraction runs in ROSTER MODE ONLY: one evidence group per CONFIRMED roster entity,
built from its assigned chunks. **There is no per-file fallback** (V3 Phase 0 deleted
it — it minted CONFIRMED, render-eligible, file-shaped entities with zero human
review): with no confirmed roster with assigned evidence, extraction refuses loudly
and returns an empty report. Run roster detection, confirm the roster, and run
assignment first (``services/roster.py``).

Flow (per entity group):

        -> extract (two-pass; heuristic default, LLM behind a flag)
        -> PAR-validate every draft
        -> FIXABLE failures bounce to re-extraction ONCE, carrying the violations
           (ABSENCE codes never bounce — a problem the evidence never stated cannot
           be re-prompted into existence)
        -> STRUCTURAL failures after the bounce are DROPPED (logged, never queued):
           unspecific problems, fragment actions, and any claim citing evidence
           outside its own group (the project boundary — cross-project Results are
           unrepresentable in roster mode and rejected everywhere)
        -> one outcome span supports at most ONE claim's Result: later claims keep
           the work, lose the Result (back to missing, flagged for review)
        -> INTEGRITY failures land in the review queue flagged (validation_flags);
           ABSENCE codes are readiness data, never persisted as flags (V3 Phase 0)
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

import hashlib
import json
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace

from app.config import Settings, get_settings
from app.domain.claims import (
    Claim,
    ClaimExtractionError,
    ClaimExtractor,
    ClaimField,
    ClaimRepository,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSeed,
    ExperienceStatus,
    ResultKind,
    ResultStatus,
    StorableClaim,
    claim_content_fingerprint,
)
from app.domain.evaluation import compute_slop_metrics
from app.domain.par_validation import ABSENCE_CODES, is_absence, is_structural, validate_claim
from app.domain.validation_runs import (
    KIND_EXTRACTION_EVAL,
    KIND_EXTRACTION_FAILURE,
    KIND_PAR_VALIDATION,
    ValidationRunLog,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionReport:
    """What one extraction run produced (per user)."""

    claims: list[Claim] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # structurally invalid, never queued
    deduped: list[str] = field(default_factory=list)  # duplicates of already-queued content
    failed_groups: list[str] = field(default_factory=list)  # extraction failed outright
    skipped_unchanged: list[str] = field(default_factory=list)  # evidence fingerprint matched
    severed_results: int = 0  # pass-2 results citing outside their batch (H7/F8)

    @property
    def flagged(self) -> list[Claim]:
        return [c for c in self.claims if c.validation_flags]

    @property
    def missing_results(self) -> list[Claim]:
        return [c for c in self.claims if c.result_kind is ResultKind.MISSING]


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

# ABSENCE_CODES (par_validation) do double duty here: a re-extraction cannot fix them
# — when the evidence states no pain point, no amount of re-prompting conjures one
# (the prompt already says "use null"), and bouncing on them doubled the LLM cost of
# every commit-heavy group for zero benefit — and they are readiness data, not claim
# defects, so they are never persisted as validation_flags either (V3 Phase 0).


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
    """Extract one group's claims, bouncing FIXABLE validator failures once.

    :data:`~app.domain.par_validation.ABSENCE_CODES` violations (the evidence states
    no pain point) never trigger the bounce — re-prompting cannot fix them and used to
    double the LLM cost of every commit-heavy group. The re-extraction carries the
    full violation list as context. After the bounce:

    * claims with STRUCTURAL violations (unspecific Problems, fragment Actions,
      evidence cited from outside the group) are dropped — not reviewable candidates;
    * one outcome span supports at most ONE claim's Result — later claims keep their
      work, lose the Result (missing + flagged);
    * INTEGRITY violations land in the queue as flags; ABSENCE violations do not
      persist at all — a missing Problem is readiness data, not a claim defect.

    A failure on the bounce itself falls back to the first pass's drafts (flagged)
    rather than losing them.
    """
    drafts = extractor.extract(group)
    violations_per_draft = [validate_claim(d) for d in drafts]

    # Bounce only when at least one violation is FIXABLE by re-extraction; the full
    # violation list still rides along as context when it does.
    fixable = [v for vs in violations_per_draft for v in vs if v.code not in ABSENCE_CODES]
    all_violations = [str(v) for vs in violations_per_draft for v in vs]
    if fixable:
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

        flags = [str(v) for v in violations if not is_absence(v)]
        result_spans = {
            (ref.chunk.source_ref, " ".join((ref.outcome_quote or "").split()))
            for ref in draft.evidence
            if ref.field is ClaimField.RESULT and (ref.outcome_quote or "").strip()
        }
        if result_spans & seen_outcome_spans:
            draft = _strip_result(draft)
            flags = [str(v) for v in validate_claim(draft) if not is_absence(v)]
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


def _dropped_draft_json(draft: DraftClaim) -> str:
    """The FULL dropped draft, reconstructable from its ``validation_runs`` row (H7/F7).

    A drop is a deliberate quality gate; losing the dropped content to an 80-char
    preview made the gate unauditable. Chunk texts are not repeated — the evidence
    refs point at persisted rows.
    """
    return json.dumps(
        {
            "action_text": draft.action_text,
            "action_tools": list(draft.action_tools),
            "problem_text": draft.problem_text,
            "problem_cost_dimension": (
                draft.problem_cost_dimension.value if draft.problem_cost_dimension else None
            ),
            "problem_inefficiency": (
                draft.problem_inefficiency.value if draft.problem_inefficiency else None
            ),
            "result_text": draft.result_text,
            "result_kind": draft.result_kind.value,
            "result_metric_json": draft.result_metric_json,
            "evidence": [
                {
                    "source_type": ref.chunk.source_type,
                    "source_ref": ref.chunk.source_ref,
                    "field": ref.field.value,
                    "outcome_quote": ref.outcome_quote,
                }
                for ref in draft.evidence
            ],
        },
        ensure_ascii=False,
    )


def _group_fingerprint(group: EvidenceGroup) -> str:
    """Stable hash of a group's evidence content (what extraction actually reads)."""
    material = "\x1e".join(
        f"{chunk.source_type}\x1f{chunk.source_ref}\x1f{chunk.chunk_text}"
        for chunk in sorted(group.chunks, key=lambda c: (c.source_type, c.source_ref))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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
    user_id: str,
    repository: ClaimRepository,
    settings: Settings | None = None,
    extractor: ClaimExtractor | None = None,
    *,
    validation_log: ValidationRunLog | None = None,
    experience_names: Collection[str] | None = None,
    force: bool = False,
) -> ExtractionReport:
    """End-to-end: build roster groups, extract, PAR-validate, persist. Idempotent.

    ROSTER MODE ONLY: one group per confirmed entity, from its assigned evidence.
    With no confirmed roster with assigned evidence the run REFUSES loudly and
    returns an empty report — the per-file fallback is gone (V3 Phase 0; it minted
    CONFIRMED file-shaped entities with zero human review, the V2 audit's root
    cause). Run roster detection, confirm the roster, and run assignment first.

    ``experience_names`` restricts the run to the named groups (case-insensitive) —
    the targeted re-run after one group failed; other entities' claims are untouched.
    Naming groups implies ``force`` for them: an explicit re-run never silently no-ops.

    A group whose evidence fingerprint matches its last successful extraction is
    SKIPPED (the queued claims already reflect it — no LLM spend); ``force=True``
    re-extracts everything regardless.

    With a ``validation_log``, every persisted claim's final PAR verdict is recorded
    as a ``validation_runs`` row (pass, or fail with the specific violations).
    """
    settings = settings or get_settings()
    if extractor is None:
        from app.services.extractor_factory import create_claim_extractor

        extractor = create_claim_extractor(settings)

    groups = gather_roster_groups(user_id, repository)
    if not groups:
        logger.warning(
            "claim extraction for %s REFUSED: no confirmed roster entities with "
            "assigned evidence. Run roster detection, confirm the roster, and run "
            "assignment first — extraction has no per-file fallback (file-shaped "
            "entities were the V2 audit's root cause).",
            user_id,
        )
        return ExtractionReport()
    logger.info(
        "claim extraction for %s: %d confirmed entity group(s)",
        user_id,
        len(groups),
    )

    if experience_names is not None:
        wanted = {name.casefold() for name in experience_names}
        groups = [g for g in groups if g.experience.name.casefold() in wanted]
        force = True  # an explicitly requested re-run never silently no-ops
        logger.info(
            "claim extraction for %s: restricted to %d group(s): %s",
            user_id,
            len(groups),
            ", ".join(g.experience.name for g in groups) or "(none matched)",
        )

    # Groups come FROM confirmed entities, so extraction only ever looks one up —
    # it is structurally incapable of creating an experience (Phase 0: nothing in
    # this service can mint a CONFIRMED entity).
    entities = {e.name.casefold(): e for e in repository.list_experiences(user_id)}

    persisted: list[Claim] = []
    dropped: list[str] = []
    deduped: list[str] = []
    failed_groups: list[str] = []
    skipped_unchanged: list[str] = []
    severed_results = 0
    for group in groups:
        experience = entities[group.experience.name.casefold()]
        group_hash = _group_fingerprint(group)
        if not force and experience.extraction_hash == group_hash:
            # The queued claims already reflect exactly this evidence: extraction
            # would reproduce them at full LLM cost. Skip (force=True overrides).
            logger.info("skipping %r: evidence unchanged since last extraction", experience.name)
            skipped_unchanged.append(experience.name)
            continue
        for chunk in group.chunks:
            repository.upsert_evidence(user_id, chunk)
        try:
            extraction = extract_and_validate_group(extractor, group)
            # H7 (F8): a batched group whose pass-2 cited outside its batch left a
            # Result honestly missing — count the loss instead of losing it silently.
            severed_results += getattr(extractor, "last_severed", 0)
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
                # H7 (F7): the row carries the WHOLE dropped draft, not a preview —
                # every deliberate drop is auditable and reconstructable after the fact.
                validation_log.record(
                    user_id,
                    KIND_PAR_VALIDATION,
                    subject_ref=f"dropped:{experience.name}",
                    passed=False,
                    detail=(*(str(v) for v in violations), _dropped_draft_json(draft)),
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
        # Only a fully successful group records its fingerprint — a failed or skipped
        # group stays eligible for the next run.
        repository.set_extraction_hash(experience.id, group_hash)

    report = ExtractionReport(
        claims=persisted,
        dropped=dropped,
        deduped=deduped,
        failed_groups=failed_groups,
        skipped_unchanged=skipped_unchanged,
        severed_results=severed_results,
    )
    if validation_log is not None and report.claims:
        # The per-run scorecard (audit Phase 4): extractor/prompt changes get a
        # tracked metric row instead of vibes. Boundary violations fail the row.
        metrics = compute_slop_metrics(report.claims, repository.get_evidence)
        validation_log.record(
            user_id,
            KIND_EXTRACTION_EVAL,
            subject_ref="extraction_run",
            passed=metrics.boundary_clean,
            detail=(*metrics.detail_lines(), f"severed_results={severed_results}"),
        )
    logger.info(
        "claim extraction for %s: %d claim(s) pending review (%d flagged, %d missing results); "
        "%d dropped as structurally invalid, %d dropped as duplicates, %d group(s) failed, "
        "%d group(s) skipped (evidence unchanged)",
        user_id,
        len(report.claims),
        len(report.flagged),
        len(report.missing_results),
        len(report.dropped),
        len(report.deduped),
        len(report.failed_groups),
        len(report.skipped_unchanged),
    )
    return report
