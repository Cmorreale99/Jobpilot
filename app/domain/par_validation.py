"""The PAR validator: deterministic checks over a draft claim and its cited evidence.

Encodes the non-negotiable rules (see ``app/domain/claims.py`` for the definitions):

* **Problem gate** - a Problem is valid only if it declares a cost dimension, an
  inefficiency, or both. A task description is NOT a problem.
* **Action gate** - an Action must name concrete tools/tech.
* **Outcome-statement rule** (content gate; there is NO source-type ban):
  ``quantified``  -> ``result_metric_json`` populated AND the metric text appears
  verbatim in the cited evidence chunk. ``qualitative_evidenced`` -> the
  ``outcome_quote`` on the claim_evidence link appears verbatim in the cited chunk.
  ``missing`` -> NEVER invent: no result text, no metric, no result links (the claim
  routes to the Missing-Results queue instead).
* **Coupling rule** - ``result_metric_json.resolves`` must appear among the claim's
  declared ``problem_cost_dimension`` / ``problem_inefficiency`` values. A mismatch is
  flagged ``"result does not address stated problem"``.

**Three violation families** (docs/V2_AUDIT.md §9 + docs/ARCHITECTURE_V3.md §3.5):

* :data:`STRUCTURAL_CODES` — the claim is not a reviewable candidate at all (a one-word
  Problem, a mid-clause Action fragment, a resume header extracted as a pain point).
  Extraction DROPS these, logged, never persisted.
* :data:`ABSENCE_CODES` — the evidence honestly states no pain point. Readiness data,
  not a defect: never bounced on, never stored as a flag (the fact stays recomputable
  from the claim itself — ``problem_text`` is empty).
* Everything else is an INTEGRITY flag — it lands in the review queue as
  ``validation_flags`` and blocks approve-as-is until edit-attested or rejected.

Verbatim means an exact, case-sensitive substring after collapsing whitespace runs
(quotes must survive text reflowing, nothing else).

Pure logic, no I/O: the caller supplies the claim with its evidence refs attached.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.claims import (
    ClaimField,
    CostDimension,
    DraftClaim,
    Inefficiency,
    ResultKind,
)

# The exact coupling-failure flag text (asserted in tests; shown on review cards).
COUPLING_FLAG = "result does not address stated problem"

# Violation codes that make a claim structurally unreviewable: extraction drops these
# pre-persistence instead of queueing them flagged.
STRUCTURAL_CODES = frozenset({"problem_not_specific", "action_fragment"})

# Absence-of-problem codes: the evidence honestly states no pain point. V3 Phase 0
# (docs/ARCHITECTURE_V3.md §3.5) reclassifies absence as READINESS data — "this project
# still needs a Problem" — not a claim defect: extraction neither bounces on these
# (re-prompting cannot conjure a problem the evidence never stated) nor persists them
# as validation_flags, so they never block approve-as-is. Everything else that flags is
# an INTEGRITY violation (ungrounded tools, result/problem coupling, duplicate outcome
# spans) and keeps blocking the approve gate.
ABSENCE_CODES = frozenset({"problem_missing", "problem_not_pain_point"})

_MIN_PROBLEM_TOKENS = 6
_MIN_ACTION_TOKENS = 5

# Resume-artifact shapes that are never pain points: contact lines, links, file names,
# and "Company — Title | Remote  Jun 2026–Present" job headers.
_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\S+@\S+\.\w+"),  # email addresses
    re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]?\d{4}\b"),  # phone numbers
    re.compile(r"\b(?:https?://|www\.|linkedin\.com/|github\.com/)", re.IGNORECASE),
    re.compile(r"\.(?:pdf|docx?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:19|20)\d{2}\s*[–—-]\s*(?:(?:19|20)\d{2}|present)\b", re.IGNORECASE
    ),  # date-range headers
)

# An Action ending on one of these (or a comma) was truncated mid-clause.
_MID_CLAUSE_ENDINGS = frozenset(
    {"and", "or", "with", "to", "of", "for", "by", "in", "on", "the", "a", "an"}
)


@dataclass(frozen=True)
class Violation:
    """One specific validator finding, machine-readable code + human message."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def verbatim_in(needle: str, haystack: str) -> bool:
    """Exact substring match, tolerant only of whitespace reflow (case-sensitive).

    Shared by the validator and the LLM extractor's grounding filter so "verbatim"
    means exactly one thing everywhere.
    """
    collapse = " ".join
    return bool(needle.strip()) and collapse(needle.split()) in collapse(haystack.split())


def is_structural(violation: Violation) -> bool:
    """True when the violation makes the claim unreviewable (drop, never queue)."""
    return violation.code in STRUCTURAL_CODES


def is_absence(violation: Violation) -> bool:
    """True when the violation reports a missing pain point (readiness data, no flag)."""
    return violation.code in ABSENCE_CODES


def _problem_specificity(claim: DraftClaim) -> list[Violation]:
    """Structural checks on the Problem text (only when one is declared)."""
    problem = (claim.problem_text or "").strip()
    if not problem:
        return []
    violations: list[Violation] = []
    if len(problem.split()) < _MIN_PROBLEM_TOKENS:
        violations.append(
            Violation(
                "problem_not_specific",
                f"the Problem ({problem!r}) is too short to describe a pain point "
                f"(< {_MIN_PROBLEM_TOKENS} words)",
            )
        )
    normalized = " ".join(problem.split()).lower()
    action_normalized = " ".join(claim.action_text.split()).lower()
    if normalized and action_normalized and normalized in action_normalized:
        violations.append(
            Violation(
                "problem_not_specific",
                "the Problem merely restates (a substring of) the Action - it names "
                "no pain point of its own",
            )
        )
    if any(pattern.search(problem) for pattern in _ARTIFACT_PATTERNS):
        violations.append(
            Violation(
                "problem_not_specific",
                "the Problem text looks like a resume artifact (contact line, link, "
                "file name, or job header), not a pain point",
            )
        )
    return violations


def _action_structure(claim: DraftClaim) -> list[Violation]:
    """Structural checks on the Action text: fragments never reach review."""
    action = claim.action_text.strip()
    if not action:
        return []  # action_missing already covers this
    violations: list[Violation] = []
    if len(action.split()) < _MIN_ACTION_TOKENS:
        violations.append(
            Violation(
                "action_fragment",
                f"the Action ({action!r}) is a fragment (< {_MIN_ACTION_TOKENS} words)",
            )
        )
    trimmed = action.rstrip(".!?")
    last_word = re.sub(r"[^\w]+$", "", trimmed.split()[-1]).lower() if trimmed.split() else ""
    if trimmed.endswith((",", ";", "-", "–", "—")) or last_word in _MID_CLAUSE_ENDINGS:
        violations.append(
            Violation(
                "action_fragment",
                f"the Action ({action!r}) ends mid-clause - it was truncated, not extracted",
            )
        )
    return violations


def _validate_problem(claim: DraftClaim) -> list[Violation]:
    if not (claim.problem_text or "").strip():
        return [
            Violation(
                "problem_missing",
                "claim declares no Problem; a pain point costing business value is required",
            )
        ]
    if claim.problem_cost_dimension is None and claim.problem_inefficiency is None:
        return [
            Violation(
                "problem_not_pain_point",
                "a task description is not a problem: the Problem must declare a "
                "cost_dimension (money|time|risk|quality|revenue), an inefficiency "
                "(manual|slow|not_working|integration_difficulty|not_user_friendly), or both",
            )
        ]
    return []


def _validate_action(claim: DraftClaim) -> list[Violation]:
    violations: list[Violation] = []
    if not claim.action_text.strip():
        violations.append(Violation("action_missing", "claim has no Action text"))
        return violations
    if not claim.action_tools:
        violations.append(
            Violation(
                "action_names_no_tools",
                "the Action must name concrete tools/tech; none were identified",
            )
        )
    else:
        lowered = claim.action_text.lower()
        for tool in claim.action_tools:
            if tool.lower() not in lowered:
                violations.append(
                    Violation(
                        "action_tool_not_in_text",
                        f"declared tool {tool!r} does not appear in the Action text",
                    )
                )
    return violations


def _validate_result(claim: DraftClaim) -> list[Violation]:
    result_links = [ref for ref in claim.evidence if ref.field is ClaimField.RESULT]

    if claim.result_kind is ResultKind.MISSING:
        if (claim.result_text or "").strip() or claim.result_metric_json or result_links:
            return [
                Violation(
                    "missing_result_filled",
                    "result_kind=missing must never carry a Result: no text, no metric, "
                    "no result evidence - the claim routes to the Missing-Results queue",
                )
            ]
        return []

    violations: list[Violation] = []
    if not (claim.result_text or "").strip():
        violations.append(
            Violation("result_text_missing", f"result_kind={claim.result_kind} needs Result text")
        )

    if not result_links:
        violations.append(
            Violation(
                "result_evidence_missing",
                "a non-missing Result must cite at least one evidence chunk (field=result)",
            )
        )

    # Every result link must carry an outcome quote found verbatim in its chunk -
    # this is what makes the evidence an OUTCOME statement, not a WORK statement.
    for ref in result_links:
        if not (ref.outcome_quote or "").strip():
            violations.append(
                Violation(
                    "outcome_quote_missing",
                    "claim_evidence.outcome_quote is required when field=result",
                )
            )
        elif not verbatim_in(ref.outcome_quote or "", ref.chunk.chunk_text):
            violations.append(
                Violation(
                    "outcome_quote_not_verbatim",
                    "the outcome_quote does not appear verbatim in the cited evidence chunk",
                )
            )

    if claim.result_kind is ResultKind.QUANTIFIED:
        metric_text = (claim.result_metric_json or {}).get("metric_text")
        if not isinstance(metric_text, str) or not metric_text.strip():
            violations.append(
                Violation(
                    "result_metric_json_missing",
                    "result_kind=quantified requires result_metric_json with metric_text",
                )
            )
        elif not any(verbatim_in(metric_text, ref.chunk.chunk_text) for ref in result_links):
            violations.append(
                Violation(
                    "result_metric_not_verbatim",
                    "the metric text does not appear verbatim in any cited evidence chunk",
                )
            )

    violations.extend(_validate_coupling(claim))
    return violations


def _validate_coupling(claim: DraftClaim) -> list[Violation]:
    """The coupling rule: the Result must resolve a pain point declared in the Problem."""
    resolves = (claim.result_metric_json or {}).get("resolves")
    declared = {
        value.value
        for value in (claim.problem_cost_dimension, claim.problem_inefficiency)
        if isinstance(value, (CostDimension, Inefficiency))
    }
    if not isinstance(resolves, str) or not resolves:
        return [
            Violation(
                "result_problem_coupling",
                f"{COUPLING_FLAG}: result_metric_json carries no 'resolves' tag naming the "
                "declared cost dimension or inefficiency the outcome addresses",
            )
        ]
    if resolves not in declared:
        return [
            Violation(
                "result_problem_coupling",
                f"{COUPLING_FLAG}: resolves={resolves!r} is not among the declared "
                f"problem values {sorted(declared) or '(none)'}",
            )
        ]
    return []


def validate_claim(claim: DraftClaim) -> list[Violation]:
    """Run every deterministic PAR check; an empty list means the claim passes."""
    return [
        *_validate_problem(claim),
        *_problem_specificity(claim),
        *_validate_action(claim),
        *_action_structure(claim),
        *_validate_result(claim),
    ]


__all__ = [
    "ABSENCE_CODES",
    "COUPLING_FLAG",
    "STRUCTURAL_CODES",
    "Violation",
    "is_absence",
    "is_structural",
    "validate_claim",
    "verbatim_in",
]
