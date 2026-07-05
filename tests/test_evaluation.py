"""The evaluation harness (audit Phase 4): slop metrics, golden set, regression fixture.

The exit property: production-damaged input (contact headers, word-per-line runs,
date-range job headers) goes through the real pipeline and scores ZERO on the failure
modes the audit documented — fragments, unspecific problems, cross-project links.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.domain.claims import (
    SOURCE_DRIVE,
    Claim,
    ClaimEvidenceLink,
    ClaimField,
    ClaimStatus,
    ResultKind,
    ResultStatus,
    StoredEvidence,
)
from app.domain.evaluation import (
    compute_slop_metrics,
    golden_examples,
    summarize_golden_set,
)
from app.domain.validation_runs import KIND_EXTRACTION_EVAL
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_extraction import run_claim_extraction
from app.services.claim_repository import InMemoryClaimRepository
from app.services.validation_run_log import InMemoryValidationRunLog

REAL_WORLD_FIXTURES = Path(__file__).parent / "fixtures" / "real_world"
GITHUB_EMPTY = Path(__file__).parent / "fixtures" / "roster" / "github"


def _claim(
    claim_id: int,
    *,
    experience_id: int = 1,
    action: str = "Automated exception triage with Python and SQL rules",
    problem: str | None = None,
    result: str | None = None,
    status: ClaimStatus = ClaimStatus.PENDING_REVIEW,
    result_status: ResultStatus = ResultStatus.UNVERIFIED,
    flags: tuple[str, ...] = (),
    review_note: str | None = None,
    evidence: tuple[ClaimEvidenceLink, ...] = (),
) -> Claim:
    return Claim(
        id=claim_id,
        user_id="u1",
        experience_id=experience_id,
        status=status,
        action_text=action,
        action_tools=("Python",),
        problem_text=problem,
        result_text=result,
        result_kind=ResultKind.MISSING if result is None else ResultKind.QUALITATIVE_EVIDENCED,
        result_status=result_status,
        validation_flags=flags,
        review_note=review_note,
        evidence=evidence,
    )


# --- slop metrics ------------------------------------------------------------------------


def test_metrics_count_the_audited_failure_modes() -> None:
    claims = [
        _claim(1, result="Cut effort in half across the team"),
        _claim(2, action="design,"),  # fragment
        _claim(3, problem="~$8M"),  # unspecific problem
        _claim(4),  # missing result
        _claim(
            5,
            action="Migrated the exporter to Snowflake with Python",
            flags=("result_problem_coupling: …",),
        ),  # flagged + missing
        _claim(6, result="Cut effort in half across the team"),  # duplicate of 1
    ]
    metrics = compute_slop_metrics(claims, lambda _evidence_id: None)
    assert metrics.total == 6
    assert metrics.fragment_actions == 1
    assert metrics.unspecific_problems == 1
    assert metrics.duplicates == 1
    assert metrics.missing_results == 4  # claims 2-5 carry no Result
    assert metrics.flagged == 1
    assert metrics.cross_project_links == 0 and metrics.boundary_clean
    assert metrics.flag_rate == 1 / 6


def test_cross_project_links_fail_the_boundary_criterion() -> None:
    foreign = StoredEvidence(
        id=7,
        user_id="u1",
        source_type=SOURCE_DRIVE,
        source_ref="drv#chars=0-10",
        chunk_text="text",
        experience_id=99,  # assigned to a DIFFERENT entity than the claim
    )
    claim = _claim(1, evidence=(ClaimEvidenceLink(evidence_id=7, field=ClaimField.RESULT),))
    metrics = compute_slop_metrics([claim], {7: foreign}.get)
    assert metrics.cross_project_links == 1
    assert not metrics.boundary_clean


def test_empty_queue_scores_clean() -> None:
    metrics = compute_slop_metrics([], lambda _evidence_id: None)
    assert metrics.total == 0 and metrics.boundary_clean
    assert metrics.flag_rate == 0.0


# --- golden set --------------------------------------------------------------------------


def test_golden_set_summarizes_decisions_and_reasons() -> None:
    claims = [
        _claim(1, status=ClaimStatus.APPROVED),
        _claim(2, status=ClaimStatus.APPROVED, result_status=ResultStatus.USER_ATTESTED),
        _claim(3, status=ClaimStatus.REJECTED, review_note="Inflated  claim"),
        _claim(4, status=ClaimStatus.REJECTED, review_note="inflated claim"),
        _claim(5),  # pending: not part of the golden set
    ]
    summary = summarize_golden_set(claims)
    assert (summary.approved, summary.rejected, summary.attested) == (2, 2, 1)
    assert summary.total == 4
    assert summary.top_rejection_reasons[0] == ("inflated claim", 2)  # normalized


def test_golden_examples_pair_output_with_verdict_and_evidence() -> None:
    stored = StoredEvidence(
        id=7,
        user_id="u1",
        source_type=SOURCE_DRIVE,
        source_ref="drv#chars=0-10",
        chunk_text="the cited text",
        experience_id=1,
    )
    decided = _claim(
        1,
        status=ClaimStatus.REJECTED,
        review_note="not my work",
        evidence=(ClaimEvidenceLink(evidence_id=7, field=ClaimField.ACTION),),
    )
    (example,) = golden_examples([decided, _claim(2)], {7: stored}.get)
    assert example["label"] == "rejected"
    assert example["review_note"] == "not my work"
    assert example["evidence"][0]["chunk_text"] == "the cited text"


# --- the run scorecard + the reality regression -------------------------------------------


async def test_extraction_run_records_a_scorecard_and_survives_mangled_reality() -> None:
    """Audit Phase 4 exit: production-shaped damage scores zero on the failure modes."""
    settings = Settings(
        gdrive_mcp_enabled=False,
        gdrive_source_folder_id="career_docs",
        gdrive_mock_fixtures_dir=str(REAL_WORLD_FIXTURES / "drive"),
        github_mcp_enabled=False,
        github_username="nobody-here",  # the github fixture contributes nothing
        github_mock_fixtures_dir=str(GITHUB_EMPTY),
    )
    repo = InMemoryClaimRepository()
    log = InMemoryValidationRunLog()
    report = await run_claim_extraction(
        MockDriveClient(REAL_WORLD_FIXTURES / "drive"),
        MockGitHubClient(GITHUB_EMPTY),
        "u1",
        repo,
        settings,
        validation_log=log,
    )

    assert report.claims  # the mangled resume still yields reviewable claims
    metrics = compute_slop_metrics(report.claims, repo.get_evidence)
    assert metrics.fragment_actions == 0  # word-per-line runs were reflowed, not split
    assert metrics.unspecific_problems == 0  # the contact/job headers never became Problems
    assert metrics.duplicates == 0
    assert metrics.boundary_clean

    # ...and the run recorded its own scorecard row.
    (run_row,) = log.list_runs("u1", KIND_EXTRACTION_EVAL)
    assert run_row.passed  # boundary_clean
    assert any(line.startswith("fragment_actions=0") for line in run_row.detail)
