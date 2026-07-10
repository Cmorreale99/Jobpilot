"""v3.1 Increment 1 regression: the Cooper.ai defensible results must be extracted.

Before v3.1 the heuristic ``_is_outcome`` only accepted a Result that had *both* a metric
*and* a canonical outcome verb (plus 4 qualitative markers), so real data-engineering
outcomes were dropped or misfiled as work:

* "Corrected overstated charge reporting from $4.01M to $2.16M" — metric, non-canonical verb
* "Removed 195K+ duplicate FedEx records" — magnitude the metric regex missed
* "Restored 100% date coverage" — coverage result, non-canonical verb
* "Delivered five production Snowflake datasets" — spelled count, delivery result
* "Enabled daily warehouse refreshes" — operational result, no number at all

These now surface as Results (``result_kind != missing``), and — the guardrail — no Result
phrase is misclassified as an Action.
"""

from __future__ import annotations

from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    HeuristicTwoPassExtractor,
    ResultKind,
)
from app.domain.par_validation import verbatim_in

from tests.fixtures.problem_spaces.cooper_ai import (
    ALL_RESULT_STATEMENTS,
    cooper_group,
)

_EXTRACTOR = HeuristicTwoPassExtractor()


def test_cooper_defensible_results_surface_as_results_not_actions() -> None:
    claims = _EXTRACTOR.extract(cooper_group())

    result_texts = [c.result_text for c in claims if c.result_kind is not ResultKind.MISSING]
    action_texts = {c.action_text for c in claims}

    # Several of Cooper.ai's real outcomes now surface as Results (they were dropped before).
    assert len(result_texts) >= 5

    # The guardrail: not one Result phrase leaked into an Action field.
    for statement in ALL_RESULT_STATEMENTS:
        assert statement not in action_texts, f"Result misclassified as Action: {statement!r}"

    # The five outcomes the plan calls out are each present as a Result, verbatim-grounded.
    for phrase in (
        "Removed 195K+ duplicate FedEx records.",
        "Restored 100% date coverage.",
        "Corrected overstated charge reporting from $4.01M to $2.16M.",
        "Delivered five production Snowflake datasets.",
        "Enabled daily warehouse refreshes.",
    ):
        assert phrase in result_texts, f"missed Cooper.ai result: {phrase!r}"


def test_cooper_quantified_results_carry_a_verbatim_metric() -> None:
    claims = _EXTRACTOR.extract(cooper_group())
    by_result = {c.result_text: c for c in claims if c.result_kind is not ResultKind.MISSING}

    for phrase in (
        "Removed 195K+ duplicate FedEx records.",
        "Restored 100% date coverage.",
        "Corrected overstated charge reporting from $4.01M to $2.16M.",
    ):
        claim = by_result[phrase]
        assert claim.result_kind is ResultKind.QUANTIFIED
        assert claim.result_metric_json is not None
        metric_text = claim.result_metric_json["metric_text"]
        result_ref = next(ref for ref in claim.evidence if ref.outcome_quote is not None)
        assert verbatim_in(metric_text, result_ref.chunk.chunk_text)


def test_cooper_non_percentage_results_are_qualitative_evidenced() -> None:
    claims = _EXTRACTOR.extract(cooper_group())
    by_result = {c.result_text: c for c in claims if c.result_kind is not ResultKind.MISSING}

    # An operational result with no number is a real, evidenced outcome — not dropped.
    refreshes = by_result["Enabled daily warehouse refreshes."]
    assert refreshes.result_kind is ResultKind.QUALITATIVE_EVIDENCED
    assert refreshes.result_metric_json is not None
    assert refreshes.result_metric_json.get("result_type") == "operational"


def test_widening_does_not_reclassify_pure_work_statements_as_results() -> None:
    """The guardrail: broadening result recognition must not turn Actions into Results.

    Each of these is a work statement — an ambiguous leading verb ("Automated"),
    a spelled count ("three"), a delivery-adjacent noun ("pipeline", "datasets") — that
    must stay an Action with ``result_kind=missing``, because none carries an actual
    outcome (no "manual", no delivered/shipped verb, no metric+outcome-verb pair).
    """
    chunk = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c900",
        "Built three microservices in Python.\n"
        "Automated the recurring upload workflow with Bash.\n"
        "Deployed the ingestion pipeline to production.\n"
        "Structured the client datasets in Snowflake.",
    )
    group = EvidenceGroup(
        experience=ExperienceSeed(name="etl", section=ExperienceSection.PROJECTS_HACKATHONS),
        chunks=(chunk,),
    )
    claims = _EXTRACTOR.extract(group)

    assert len(claims) == 4
    assert all(c.result_kind is ResultKind.MISSING for c in claims)
    assert all(c.result_metric_json is None for c in claims)
