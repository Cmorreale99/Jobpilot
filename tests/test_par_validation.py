"""PAR validator tests: every non-negotiable rule, encoded and asserted.

These are the M8 definition tests — problem gate, action gate, the outcome-statement
rule (quantified verbatim metric / qualitative verbatim quote / missing never filled),
and the coupling rule with its exact flag text.
"""

from __future__ import annotations

from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    ClaimEvidenceRef,
    ClaimField,
    CostDimension,
    DraftClaim,
    EvidenceChunk,
    Inefficiency,
    ResultKind,
)
from app.domain.par_validation import COUPLING_FLAG, validate_claim, verbatim_in

_RESULT_CHUNK = EvidenceChunk(
    SOURCE_GITHUB_COMMIT,
    "c002",
    "Cut weekly reconciliation effort from 10 hours to 1 hour.",
)
_OUTCOME = "Cut weekly reconciliation effort from 10 hours to 1 hour."


def _valid_claim(**overrides: object) -> DraftClaim:
    """A claim that passes every gate; tests break exactly one rule at a time."""
    base = dict(
        action_text="Automated exception triage with Python and SQL rules",
        action_tools=("Python", "SQL"),
        problem_text="Manual reconciliation of shipment exceptions took 10 hours per week",
        problem_cost_dimension=CostDimension.TIME,
        problem_inefficiency=Inefficiency.MANUAL,
        result_text=_OUTCOME,
        result_kind=ResultKind.QUANTIFIED,
        result_metric_json={"resolves": "time", "metric_text": _OUTCOME},
        evidence=(
            ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.ACTION),
            ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.PROBLEM),
            ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.RESULT, outcome_quote=_OUTCOME),
        ),
    )
    base.update(overrides)
    return DraftClaim(**base)  # type: ignore[arg-type]


def _codes(claim: DraftClaim) -> set[str]:
    return {v.code for v in validate_claim(claim)}


# --- the full pass -----------------------------------------------------------------


def test_fully_evidenced_claim_passes() -> None:
    assert validate_claim(_valid_claim()) == []


# --- problem gate ------------------------------------------------------------------


def test_problem_without_dimension_or_inefficiency_is_a_task_description() -> None:
    claim = _valid_claim(
        problem_text="Needed a data pipeline",
        problem_cost_dimension=None,
        problem_inefficiency=None,
        # keep the result coupled to nothing so only the problem gate fires
        result_metric_json={"resolves": "time", "metric_text": _OUTCOME},
    )
    codes = _codes(claim)
    assert "problem_not_pain_point" in codes


def test_missing_problem_is_flagged() -> None:
    claim = _valid_claim(problem_text=None, problem_cost_dimension=None, problem_inefficiency=None)
    assert "problem_missing" in _codes(claim)


def test_problem_valid_via_cost_dimension_only() -> None:
    claim = _valid_claim(problem_inefficiency=None)
    assert "problem_not_pain_point" not in _codes(claim)


def test_problem_valid_via_inefficiency_only() -> None:
    claim = _valid_claim(
        problem_cost_dimension=None,
        result_metric_json={"resolves": "manual", "metric_text": _OUTCOME},
    )
    assert validate_claim(claim) == []


# --- action gate -------------------------------------------------------------------


def test_action_must_name_tools() -> None:
    claim = _valid_claim(action_tools=())
    assert "action_names_no_tools" in _codes(claim)


def test_declared_tool_must_appear_in_action_text() -> None:
    claim = _valid_claim(action_tools=("Python", "Kubernetes"))
    assert "action_tool_not_in_text" in _codes(claim)


# --- outcome-statement rule ----------------------------------------------------------


def test_quantified_requires_metric_json() -> None:
    claim = _valid_claim(result_metric_json={"resolves": "time"})
    assert "result_metric_json_missing" in _codes(claim)


def test_quantified_metric_must_be_verbatim_in_cited_chunk() -> None:
    claim = _valid_claim(
        result_metric_json={
            "resolves": "time",
            "metric_text": "cut effort from 10 hours to 30 minutes",
        }
    )
    assert "result_metric_not_verbatim" in _codes(claim)


def test_result_link_requires_outcome_quote() -> None:
    claim = _valid_claim(
        evidence=(
            ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.ACTION),
            ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.RESULT),
        )
    )
    assert "outcome_quote_missing" in _codes(claim)


def test_outcome_quote_must_be_verbatim_in_cited_chunk() -> None:
    claim = _valid_claim(
        evidence=(
            ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.ACTION),
            ClaimEvidenceRef(
                chunk=_RESULT_CHUNK,
                field=ClaimField.RESULT,
                outcome_quote="ops no longer reconciles manually",
            ),
        )
    )
    assert "outcome_quote_not_verbatim" in _codes(claim)


def test_qualitative_evidenced_with_verbatim_quote_passes() -> None:
    chunk = EvidenceChunk(
        SOURCE_DRIVE,
        "drv1",
        "Manual reconciliation was eating the week. Eliminated duplicate shipment rows; "
        "ops no longer reconciles manually.",
    )
    claim = _valid_claim(
        result_text="Eliminated duplicate shipment rows; ops no longer reconciles manually.",
        result_kind=ResultKind.QUALITATIVE_EVIDENCED,
        result_metric_json={"resolves": "manual"},
        evidence=(
            ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),
            ClaimEvidenceRef(
                chunk=chunk,
                field=ClaimField.RESULT,
                outcome_quote=(
                    "Eliminated duplicate shipment rows; ops no longer reconciles manually."
                ),
            ),
        ),
    )
    assert validate_claim(claim) == []


def test_non_missing_result_needs_result_evidence() -> None:
    claim = _valid_claim(evidence=(ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.ACTION),))
    assert "result_evidence_missing" in _codes(claim)


# --- missing means missing -----------------------------------------------------------


def test_missing_result_with_no_content_passes() -> None:
    claim = _valid_claim(
        result_text=None,
        result_kind=ResultKind.MISSING,
        result_metric_json=None,
        evidence=(ClaimEvidenceRef(chunk=_RESULT_CHUNK, field=ClaimField.ACTION),),
    )
    assert validate_claim(claim) == []


def test_missing_result_must_never_be_filled() -> None:
    claim = _valid_claim(result_kind=ResultKind.MISSING)
    assert "missing_result_filled" in _codes(claim)


# --- coupling rule -------------------------------------------------------------------


def test_result_resolving_undeclared_dimension_is_flagged() -> None:
    claim = _valid_claim(result_metric_json={"resolves": "revenue", "metric_text": _OUTCOME})
    violations = validate_claim(claim)
    assert any(v.code == "result_problem_coupling" for v in violations)
    assert any(COUPLING_FLAG in v.message for v in violations)


def test_result_without_resolves_tag_is_flagged() -> None:
    claim = _valid_claim(result_metric_json={"metric_text": _OUTCOME})
    assert any(COUPLING_FLAG in v.message for v in validate_claim(claim))


def test_resolves_may_match_the_declared_inefficiency() -> None:
    claim = _valid_claim(result_metric_json={"resolves": "manual", "metric_text": _OUTCOME})
    assert validate_claim(claim) == []


# --- verbatim helper -----------------------------------------------------------------


def test_verbatim_tolerates_whitespace_reflow_only() -> None:
    assert verbatim_in("cut  failure\nrate", "We cut failure rate in half")
    assert not verbatim_in("Cut Failure Rate", "we cut failure rate in half")
    assert not verbatim_in("", "anything")
