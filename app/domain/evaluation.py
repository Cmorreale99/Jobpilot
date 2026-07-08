"""The extraction evaluation harness: slop metrics + the golden set (pure logic).

Audit Phase 4 (docs/V2_AUDIT.md §11.4): extractor and prompt changes get a scorecard
instead of vibes. Two instruments:

* :func:`compute_slop_metrics` — deterministic rates over a set of claims: fragments,
  unspecific problems, duplicates, missing results, flags, and cross-project evidence
  links (which must be ZERO — the roster boundary makes them unrepresentable in
  extraction, so any non-zero count is a regression or legacy data).
* :func:`summarize_golden_set` — the labeled corpus the review layer accumulates for
  free: every retained approve/reject/edit decision is a training-quality label on a
  real extraction output. :func:`golden_examples` exports them.

Pure and offline: callers supply the claims and an evidence resolver; nothing here
touches the DB, the network, or a model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimStatus,
    DraftClaim,
    ResultKind,
    ResultStatus,
    StoredEvidence,
    claim_content_fingerprint,
)
from app.domain.par_validation import is_structural, validate_claim
from app.domain.project_story import (
    ComponentPresence,
    QuestionKind,
    StoryReadiness,
    StoryReviewStatus,
    StoryViolation,
)

EvidenceResolver = Callable[[int], StoredEvidence | None]

# Structural violation codes that mean a component points nowhere valid (must be 0).
_ORPHAN_CODES = frozenset({"orphan_component_ref", "cross_entity_ref", "component_without_refs"})


@dataclass(frozen=True)
class SlopMetrics:
    """One extraction state, scored. Rates are fractions of ``total`` (0 when empty)."""

    total: int
    fragment_actions: int  # structural action failures (should be impossible post-gate)
    unspecific_problems: int  # structural problem failures (ditto)
    duplicates: int  # claims sharing a content fingerprint with an earlier claim
    missing_results: int
    flagged: int
    cross_project_links: int  # evidence assigned to a DIFFERENT entity than the claim

    @property
    def fragment_rate(self) -> float:
        return self.fragment_actions / self.total if self.total else 0.0

    @property
    def duplicate_rate(self) -> float:
        return self.duplicates / self.total if self.total else 0.0

    @property
    def missing_result_rate(self) -> float:
        return self.missing_results / self.total if self.total else 0.0

    @property
    def flag_rate(self) -> float:
        return self.flagged / self.total if self.total else 0.0

    @property
    def boundary_clean(self) -> bool:
        """The hard pass/fail criterion: zero cross-project evidence links."""
        return self.cross_project_links == 0

    def detail_lines(self) -> tuple[str, ...]:
        """Human-and-grep-readable lines (stored in ``validation_runs.detail``)."""
        return (
            f"total={self.total}",
            f"fragment_actions={self.fragment_actions}",
            f"unspecific_problems={self.unspecific_problems}",
            f"duplicates={self.duplicates}",
            f"missing_results={self.missing_results} ({self.missing_result_rate:.0%})",
            f"flagged={self.flagged} ({self.flag_rate:.0%})",
            f"cross_project_links={self.cross_project_links}",
        )


def _structural_counts(claim: Claim) -> tuple[bool, bool]:
    """(is_fragment_action, is_unspecific_problem) via the real structural validators."""
    draft = DraftClaim(
        action_text=claim.action_text,
        action_tools=claim.action_tools,
        problem_text=claim.problem_text,
        problem_cost_dimension=claim.problem_cost_dimension,
        problem_inefficiency=claim.problem_inefficiency,
    )
    structural = [v for v in validate_claim(draft) if is_structural(v)]
    return (
        any(v.code == "action_fragment" for v in structural),
        any(v.code == "problem_not_specific" for v in structural),
    )


def compute_slop_metrics(
    claims: Sequence[Claim], resolve_evidence: EvidenceResolver
) -> SlopMetrics:
    """Score a set of claims (typically one user's unreviewed queue, or one run's output)."""
    fragments = 0
    unspecific = 0
    duplicates = 0
    missing = 0
    flagged = 0
    cross_project = 0
    seen_fingerprints: set[tuple[str, str, str]] = set()

    for claim in claims:
        is_fragment, is_unspecific = _structural_counts(claim)
        fragments += 1 if is_fragment else 0
        unspecific += 1 if is_unspecific else 0

        fingerprint = claim_content_fingerprint(
            claim.problem_text, claim.action_text, claim.result_text
        )
        if fingerprint in seen_fingerprints:
            duplicates += 1
        seen_fingerprints.add(fingerprint)

        missing += 1 if claim.result_kind is ResultKind.MISSING else 0
        flagged += 1 if claim.validation_flags else 0

        for link in claim.evidence:
            evidence = resolve_evidence(link.evidence_id)
            if evidence is None or evidence.source_type == SOURCE_USER_ATTESTATION:
                continue
            if evidence.experience_id is not None and evidence.experience_id != claim.experience_id:
                cross_project += 1

    return SlopMetrics(
        total=len(claims),
        fragment_actions=fragments,
        unspecific_problems=unspecific,
        duplicates=duplicates,
        missing_results=missing,
        flagged=flagged,
        cross_project_links=cross_project,
    )


@dataclass(frozen=True)
class GoldenSetSummary:
    """How much labeled review data the ledger has accumulated."""

    approved: int
    rejected: int
    attested: int  # approved with a user-attested Result (edit-resolved)
    top_rejection_reasons: tuple[tuple[str, int], ...]

    @property
    def total(self) -> int:
        return self.approved + self.rejected


def summarize_golden_set(claims: Iterable[Claim], *, top_reasons: int = 5) -> GoldenSetSummary:
    approved = rejected = attested = 0
    reasons: Counter[str] = Counter()
    for claim in claims:
        if claim.status is ClaimStatus.APPROVED:
            approved += 1
            if claim.result_status is ResultStatus.USER_ATTESTED:
                attested += 1
        elif claim.status is ClaimStatus.REJECTED:
            rejected += 1
            if claim.review_note:
                reasons[" ".join(claim.review_note.split()).lower()] += 1
    return GoldenSetSummary(
        approved=approved,
        rejected=rejected,
        attested=attested,
        top_rejection_reasons=tuple(reasons.most_common(top_reasons)),
    )


def golden_examples(
    claims: Iterable[Claim], resolve_evidence: EvidenceResolver
) -> list[dict[str, Any]]:
    """The labeled corpus, one JSON-serializable example per decided claim.

    Each example pairs the extraction output (P/A/R + cited evidence text) with the
    human verdict — exactly the shape a future prompt-tuning or judge step consumes.
    """
    examples: list[dict[str, Any]] = []
    for claim in claims:
        if claim.status not in (ClaimStatus.APPROVED, ClaimStatus.REJECTED):
            continue
        evidence = []
        for link in claim.evidence:
            stored = resolve_evidence(link.evidence_id)
            if stored is None:
                continue
            evidence.append(
                {
                    "field": link.field.value,
                    "source_type": stored.source_type,
                    "source_ref": stored.source_ref,
                    "chunk_text": stored.chunk_text,
                    "outcome_quote": link.outcome_quote,
                }
            )
        examples.append(
            {
                "claim_id": claim.id,
                "experience_id": claim.experience_id,
                "label": claim.status.value,
                "review_note": claim.review_note,
                "attested": claim.result_status is ResultStatus.USER_ATTESTED,
                "problem": claim.problem_text,
                "action": claim.action_text,
                "action_tools": list(claim.action_tools),
                "result": claim.result_text,
                "result_kind": claim.result_kind.value,
                "validation_flags": list(claim.validation_flags),
                "evidence": evidence,
            }
        )
    return examples


# --- Story evaluation (V3 Phase 5b, ARCHITECTURE_V3 §5) -----------------------------------


@dataclass(frozen=True)
class StoryEvalInput:
    """One story's evaluable state: its review status, derived readiness, structural
    violations, and normalized Result outcome spans (for cross-story duplicate detection)."""

    status: StoryReviewStatus
    readiness: StoryReadiness
    violations: Sequence[StoryViolation]
    result_spans: Sequence[str]


@dataclass(frozen=True)
class StoryEvalReport:
    """A story-synthesis run's scorecard (recorded to ``validation_runs`` kind story_eval)."""

    total: int
    approved: int
    pending_review: int
    draft: int
    excluded: int
    resume_ready: int
    missing_problem: int
    missing_result_only: int
    missing_both: int
    questions_outstanding: int
    invented_metric_count: int  # MUST be 0
    orphan_component_count: int  # MUST be 0
    duplicate_story_count: int  # MUST be 0
    cross_source_conflict_count: int

    @property
    def resume_ready_rate(self) -> float:
        return self.resume_ready / self.total if self.total else 0.0

    @property
    def boundary_clean(self) -> bool:
        """The hard pass/fail: no invented metrics, no orphan components, no duplicate stories."""
        return (
            self.invented_metric_count == 0
            and self.orphan_component_count == 0
            and self.duplicate_story_count == 0
        )

    def detail_lines(self) -> tuple[str, ...]:
        return (
            f"total={self.total}",
            f"approved={self.approved}",
            f"pending_review={self.pending_review}",
            f"draft={self.draft}",
            f"excluded={self.excluded}",
            f"resume_ready={self.resume_ready} ({self.resume_ready_rate:.0%})",
            f"missing_problem={self.missing_problem}",
            f"missing_result_only={self.missing_result_only}",
            f"missing_both={self.missing_both}",
            f"questions_outstanding={self.questions_outstanding}",
            f"invented_metric_count={self.invented_metric_count}",
            f"orphan_component_count={self.orphan_component_count}",
            f"duplicate_story_count={self.duplicate_story_count}",
            f"cross_source_conflict_count={self.cross_source_conflict_count}",
        )


def summarize_story_eval(inputs: Sequence[StoryEvalInput]) -> StoryEvalReport:
    """Score a set of stories against the §5 metrics (pure; the caller supplies readiness).

    ``duplicate_story_count`` counts outcome spans that back a Result under more than one
    story — the final-CV dedup invariant restated as a metric (must be 0). ``invented_metric``
    and ``orphan_component`` counts are stories carrying the corresponding fatal structural
    violation (also must be 0). Questions are counted only for undecided stories.
    """
    status_counts: Counter[StoryReviewStatus] = Counter()
    resume_ready = missing_problem = missing_result_only = missing_both = 0
    questions_outstanding = invented = orphan = conflicts = 0
    span_owners: dict[str, set[int]] = {}

    for index, item in enumerate(inputs):
        status_counts[item.status] += 1
        readiness = item.readiness
        if readiness.resume_ready:
            resume_ready += 1
        # Mutually exclusive gap buckets: both > problem-only > result-only.
        problem_missing = readiness.problem is ComponentPresence.MISSING
        result_missing = readiness.result is ComponentPresence.MISSING
        if problem_missing and result_missing:
            missing_both += 1
        elif problem_missing:
            missing_problem += 1
        elif result_missing:
            missing_result_only += 1

        if item.status in (StoryReviewStatus.DRAFT, StoryReviewStatus.PENDING_REVIEW):
            questions_outstanding += len(readiness.questions)
        conflicts += sum(1 for q in readiness.questions if q.kind is QuestionKind.METRIC_CONFLICT)

        codes = {v.code for v in item.violations}
        if "unsupported_number" in codes:
            invented += 1
        if codes & _ORPHAN_CODES:
            orphan += 1

        for span in item.result_spans:
            if span:
                span_owners.setdefault(span, set()).add(index)

    duplicate_story_count = sum(1 for owners in span_owners.values() if len(owners) > 1)

    return StoryEvalReport(
        total=len(inputs),
        approved=status_counts[StoryReviewStatus.APPROVED],
        pending_review=status_counts[StoryReviewStatus.PENDING_REVIEW],
        draft=status_counts[StoryReviewStatus.DRAFT],
        excluded=status_counts[StoryReviewStatus.EXCLUDED],
        resume_ready=resume_ready,
        missing_problem=missing_problem,
        missing_result_only=missing_result_only,
        missing_both=missing_both,
        questions_outstanding=questions_outstanding,
        invented_metric_count=invented,
        orphan_component_count=orphan,
        duplicate_story_count=duplicate_story_count,
        cross_source_conflict_count=conflicts,
    )


__all__ = [
    "EvidenceResolver",
    "GoldenSetSummary",
    "SlopMetrics",
    "StoryEvalInput",
    "StoryEvalReport",
    "compute_slop_metrics",
    "golden_examples",
    "summarize_golden_set",
    "summarize_story_eval",
]
