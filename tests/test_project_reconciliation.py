"""v3.1 Increment 5 — expected-project reconciliation.

The audit's gap #6: an expected project the sources never mention must surface as
``missing_from_resume_or_source_not_loaded`` (fix outside the system), distinctly from
a parsing gap (name present in a source, no confirmed entity — fix inside the system),
and never be silently omitted or misread as a parsing failure. The exit case is the
live corpus's ``paper_recommender_system``.
"""

from __future__ import annotations

from app.config import Settings
from app.domain.claims import (
    ClaimRepository,
    Experience,
    ExperienceSection,
    ExperienceSeed,
)
from app.domain.project_reconciliation import (
    NEXT_ACTION_REVIEW_PARSING,
    NEXT_ACTION_SEARCH_OR_ASK,
    STATUS_DETECTED,
    STATUS_MISSING,
    STATUS_PARSING_GAP,
    reconcile_expected_projects,
)
from app.domain.validation_runs import KIND_PROJECT_RECONCILIATION
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_repository import InMemoryClaimRepository
from app.services.roster import run_project_reconciliation
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.conftest import confirm_source_roster


def _entity(repo: ClaimRepository, name: str, aliases: tuple[str, ...] = ()) -> Experience:
    return repo.upsert_experience(
        "u1",
        ExperienceSeed(name=name, section=ExperienceSection.PROJECTS_HACKATHONS, aliases=aliases),
    )


# --- pure reconciliation -------------------------------------------------------------------


def test_detected_by_name_alias_and_identifier_form() -> None:
    repo = InMemoryClaimRepository()
    oneworld = _entity(repo, "OneWorld", aliases=("cmorreale99/oneworld",))
    paper = _entity(repo, "Paper recommender system")
    entities = [oneworld, paper]

    results = reconcile_expected_projects(
        ["oneworld", "cmorreale99/oneworld", "paper_recommender_system"], entities, []
    )

    assert [r.status for r in results] == [STATUS_DETECTED] * 3
    assert all(r.detected_in_resume for r in results)
    assert results[0].matched_experience_id == oneworld.id
    assert results[1].matched_experience_name == "OneWorld"
    # The snake_case identifier reconciles against the spaced display name.
    assert results[2].matched_experience_id == paper.id
    assert results[2].to_payload()["matched_experience"] == {
        "id": paper.id,
        "name": "Paper recommender system",
    }


def test_mentioned_but_unparsed_is_a_parsing_gap_not_missing() -> None:
    texts = ["Worked at Northwind Payments rebuilding the settlement pipeline."]
    [result] = reconcile_expected_projects(["northwind_payments"], [], texts)

    assert result.status == STATUS_PARSING_GAP
    assert result.detected_in_resume is False
    assert result.next_action == NEXT_ACTION_REVIEW_PARSING


def test_absent_everywhere_is_the_exact_missing_payload() -> None:
    """The plan's exit case: ``paper_recommender_system`` absent from parsed text is a
    fact about the sources — the exact spec JSON, never a parsing failure."""
    repo = InMemoryClaimRepository()
    entities = [_entity(repo, "OneWorld")]
    texts = ["OneWorld hackathon translation assistant, Top 3 out of 100+ teams."]

    [result] = reconcile_expected_projects(["paper_recommender_system"], entities, texts)

    assert result.to_payload() == {
        "expected_project": "paper_recommender_system",
        "detected_in_resume": False,
        "status": "missing_from_resume_or_source_not_loaded",
        "next_action": "search_project_sources_or_ask_user_to_add",
    }


def test_every_expected_project_gets_exactly_one_result_in_order() -> None:
    repo = InMemoryClaimRepository()
    entities = [_entity(repo, "fraud-stream")]
    texts = ["Northwind Payments settlement work."]

    expected = ["fraud-stream", "northwind_payments", "paper_recommender_system"]
    results = reconcile_expected_projects(expected, entities, texts)

    assert [r.expected_project for r in results] == expected
    assert [r.status for r in results] == [STATUS_DETECTED, STATUS_PARSING_GAP, STATUS_MISSING]
    assert results[0].next_action is None
    assert results[1].next_action == NEXT_ACTION_REVIEW_PARSING
    assert results[2].next_action == NEXT_ACTION_SEARCH_OR_ASK


# --- the service wrapper over roster + gathered sources -----------------------------------


async def test_run_project_reconciliation_over_fixture_sources(
    mock_client: MockDriveClient,
    mock_github_client: MockGitHubClient,
    settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    log = InMemoryValidationRunLog()
    await confirm_source_roster(mock_client, mock_github_client, "u1", repo, settings)

    report = await run_project_reconciliation(
        mock_client,
        mock_github_client,
        "u1",
        repo,
        ["fraud-stream", "Northwind Payments", "paper_recommender_system"],
        settings,
        validation_log=log,
    )

    by_name = {r.expected_project: r for r in report.results}
    assert by_name["fraud-stream"].status == STATUS_DETECTED
    assert by_name["fraud-stream"].detected_in_resume is True
    # In the resume's raw text but confirmed under no entity: a parsing/roster gap.
    assert by_name["Northwind Payments"].status == STATUS_PARSING_GAP
    # Nowhere at all: the honest missing status, not a parsing failure.
    assert by_name["paper_recommender_system"].status == STATUS_MISSING
    assert [r.expected_project for r in report.undetected] == [
        "Northwind Payments",
        "paper_recommender_system",
    ]

    [run] = log.list_runs("u1", KIND_PROJECT_RECONCILIATION)
    assert run.passed is False
    assert run.subject_ref == "expected_projects"
    assert any("paper_recommender_system" in line and STATUS_MISSING in line for line in run.detail)
    assert any("Northwind Payments" in line and STATUS_PARSING_GAP in line for line in run.detail)


async def test_run_project_reconciliation_passes_when_everything_is_detected(
    mock_client: MockDriveClient,
    mock_github_client: MockGitHubClient,
    settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    log = InMemoryValidationRunLog()
    await confirm_source_roster(mock_client, mock_github_client, "u1", repo, settings)

    report = await run_project_reconciliation(
        mock_client,
        mock_github_client,
        "u1",
        repo,
        ["fraud-stream", "payments-ledger"],
        settings,
        validation_log=log,
    )

    assert report.undetected == []
    [run] = log.list_runs("u1", KIND_PROJECT_RECONCILIATION)
    assert run.passed is True
    assert run.detail == ()
