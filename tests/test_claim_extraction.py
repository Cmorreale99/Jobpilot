"""Heuristic two-pass extractor tests, including the M8 exit tests.

Exit tests (from the V2 build order):
1. A fixture commit with only work statements produces a claim with
   ``result_kind=missing`` — never a filled Result.
2. A fixture commit containing "reduced X from A to B" produces ``quantified``
   with a verbatim metric match.
3. A Problem declaring ``slow`` with a Result resolving ``revenue`` produces the
   coupling failure flag ("result does not address stated problem").
"""

from __future__ import annotations

from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    ClaimField,
    CostDimension,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    HeuristicTwoPassExtractor,
    Inefficiency,
    ResultKind,
)
from app.domain.par_validation import COUPLING_FLAG, validate_claim, verbatim_in

_EXTRACTOR = HeuristicTwoPassExtractor()


def _group(*chunks: EvidenceChunk) -> EvidenceGroup:
    return EvidenceGroup(
        experience=ExperienceSeed(
            name="carrier-etl", section=ExperienceSection.PROJECTS_HACKATHONS
        ),
        chunks=chunks,
    )


# --- EXIT TEST 1: work statements never fill a Result --------------------------------


def test_commit_with_only_work_statements_yields_missing_result() -> None:
    chunk = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c001",
        "Fixed dedup key mismatch across four carriers in the Kafka ingest path",
    )
    claims = _EXTRACTOR.extract(_group(chunk))

    assert len(claims) == 1
    claim = claims[0]
    assert claim.result_kind is ResultKind.MISSING
    assert claim.result_text is None
    assert claim.result_metric_json is None
    assert all(ref.field is not ClaimField.RESULT for ref in claim.evidence)
    # The work statement became Action evidence, as the content gate demands.
    assert claim.action_text.startswith("Fixed dedup key mismatch")


# --- EXIT TEST 2: "reduced X from A to B" -> quantified with verbatim metric ----------


def test_reduced_from_a_to_b_commit_yields_quantified_with_verbatim_metric() -> None:
    work = EvidenceChunk(
        SOURCE_GITHUB_README,
        "jordanrivera/deploys",
        "- Parallelized the release build with GitHub Actions matrix jobs.",
    )
    outcome = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c777",
        "Reduced deploy time from 45 minutes to 6 minutes.",
    )
    claims = _EXTRACTOR.extract(_group(work, outcome))

    assert len(claims) == 1
    claim = claims[0]
    assert claim.result_kind is ResultKind.QUANTIFIED
    assert claim.result_metric_json is not None
    metric_text = claim.result_metric_json["metric_text"]
    assert verbatim_in(metric_text, outcome.chunk_text)

    result_refs = [ref for ref in claim.evidence if ref.field is ClaimField.RESULT]
    assert len(result_refs) == 1
    assert result_refs[0].outcome_quote is not None
    assert verbatim_in(result_refs[0].outcome_quote, outcome.chunk_text)


# --- EXIT TEST 3: slow Problem + revenue Result -> coupling flag ----------------------


def test_slow_problem_with_revenue_result_trips_coupling_rule() -> None:
    chunk = EvidenceChunk(
        SOURCE_DRIVE,
        "drv_ci_002",
        "Problem: Nightly builds were slow, blocking every release.\n"
        "Action: Parallelized the integration suite with GitHub Actions matrix builds.\n"
        "Result: Increased subscription revenue by 20%.",
    )
    claims = _EXTRACTOR.extract(_group(chunk))

    assert len(claims) == 1
    claim = claims[0]
    assert claim.problem_inefficiency is Inefficiency.SLOW
    assert claim.result_kind is ResultKind.QUANTIFIED
    assert claim.result_metric_json is not None
    assert claim.result_metric_json["resolves"] == "revenue"

    violations = validate_claim(claim)
    assert any(v.code == "result_problem_coupling" for v in violations)
    assert any(COUPLING_FLAG in v.message for v in violations)


# --- two-pass behavior ----------------------------------------------------------------


def test_labeled_par_block_yields_a_fully_valid_claim() -> None:
    chunk = EvidenceChunk(
        SOURCE_DRIVE,
        "drv_recon_001",
        "Problem: Manual reconciliation of shipment exceptions took 10 hours per week.\n"
        "Action: Automated exception triage with Python and SQL rules over the carrier feed.\n"
        "Result: Cut weekly reconciliation effort from 10 hours to 1 hour.",
    )
    claims = _EXTRACTOR.extract(_group(chunk))

    assert len(claims) == 1
    claim = claims[0]
    assert claim.problem_cost_dimension is CostDimension.TIME
    assert claim.problem_inefficiency is Inefficiency.MANUAL
    assert claim.action_tools == ("Python", "SQL")
    assert claim.result_kind is ResultKind.QUANTIFIED
    assert claim.result_metric_json is not None
    assert claim.result_metric_json["resolves"] == "time"
    assert validate_claim(claim) == []


def test_labeled_result_that_is_a_work_statement_never_fills_the_result() -> None:
    chunk = EvidenceChunk(
        SOURCE_DRIVE,
        "drv_reports",
        "Problem: Manual reporting took hours each week.\n"
        "Action: Automated the report generation with Python.\n"
        "Result: Fixed the report generator crash.",
    )
    claims = _EXTRACTOR.extract(_group(chunk))

    assert len(claims) == 1
    # The labeled "Result" is a work statement; the label grants no exemption.
    assert claims[0].result_kind is ResultKind.MISSING
    assert claims[0].result_text is None


def test_qualitative_outcome_statement_becomes_qualitative_evidenced() -> None:
    work = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c010",
        "Implemented idempotent settlement writes in Go with SQL upserts",
    )
    outcome = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c011",
        "Eliminated duplicate shipment rows; ops no longer reconciles manually",
    )
    claims = _EXTRACTOR.extract(_group(work, outcome))

    assert len(claims) == 1
    claim = claims[0]
    assert claim.result_kind is ResultKind.QUALITATIVE_EVIDENCED
    result_refs = [ref for ref in claim.evidence if ref.field is ClaimField.RESULT]
    assert result_refs[0].outcome_quote is not None
    assert verbatim_in(result_refs[0].outcome_quote, outcome.chunk_text)


def test_outcome_in_same_chunk_attaches_to_that_chunks_claim() -> None:
    chunk = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c002",
        "Backfilled the missing upstream dataset behind nightly scoring\n\n"
        "Cut ingestion failure rate from 12% to 0.5% across the Python scoring jobs.",
    )
    other = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c003",
        "Fixed dedup key mismatch across four carriers in the Kafka ingest path",
    )
    claims = _EXTRACTOR.extract(_group(other, chunk))

    by_action = {c.action_text: c for c in claims}
    backfill = by_action["Backfilled the missing upstream dataset behind nightly scoring"]
    dedup = by_action["Fixed dedup key mismatch across four carriers in the Kafka ingest path"]
    assert backfill.result_kind is ResultKind.QUANTIFIED
    assert dedup.result_kind is ResultKind.MISSING


def test_problem_context_applies_forward_only_within_a_chunk() -> None:
    chunk = EvidenceChunk(
        SOURCE_DRIVE,
        "drv_two",
        "Built the ingestion CLI in Python.\n"
        "Manual uploads were costing the team hours every week.\n"
        "Automated the upload flow with Bash and Python.",
    )
    claims = _EXTRACTOR.extract(_group(chunk))

    by_action = {c.action_text: c for c in claims}
    assert by_action["Built the ingestion CLI in Python."].problem_text is None
    automated = by_action["Automated the upload flow with Bash and Python."]
    assert automated.problem_text == "Manual uploads were costing the team hours every week."


def test_extraction_is_deterministic_and_ignores_violation_feedback() -> None:
    chunk = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "c001",
        "Fixed dedup key mismatch across four carriers in the Kafka ingest path",
    )
    group = _group(chunk)
    first = _EXTRACTOR.extract(group)
    second = _EXTRACTOR.extract(group, violations=["problem_missing: whatever"])
    assert first == second
