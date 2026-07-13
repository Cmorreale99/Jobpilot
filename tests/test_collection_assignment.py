"""Collection-repository assignment truth cases (MASTER CV REPAIR §4.6-4.9, §7.4,
§11.1, §16.2/16.6/16.7).

Source truth: `Cameron-Morreale-portfolio` is a COLLECTION of projects, not a project.
Its root README sections describing child projects (OneWorld, Paper Recommender) belong
to those children; repo-wide commits stay unresolved/supporting-only; repository alias
matching never decides ownership by first match — multiple matches are ambiguity, left
visible and unassigned. A single-project repository whose one confirmed entity carries
the repo alias remains the deterministic user-approved boundary (§3.8).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.domain.claims import (
    ASSIGNMENT_README_REF,
    ASSIGNMENT_REPO_REF,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_DOC,
    SOURCE_GITHUB_README,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    split_span_ref,
)
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_repository import InMemoryClaimRepository
from app.services.roster import run_roster_assignment

from tests.test_github_universe import EmptyDriveClient

FIXTURES = Path(__file__).parent / "fixtures" / "github_universe"
USER = "u-universe"
PORTFOLIO = "cmorreale/Cameron-Morreale-portfolio"
NESTED_PR = f"{PORTFOLIO}/projects/paper-recommender/README.md"
NESTED_OW = f"{PORTFOLIO}/projects/oneworld/README.md"


def _settings() -> Settings:
    return Settings(
        github_mcp_enabled=False,
        github_username="cmorreale",
        github_mock_fixtures_dir=str(FIXTURES),
        gdrive_source_folder_id="",
        uploads_dir="",
    )


def _confirm(repo: InMemoryClaimRepository, name: str, *aliases: str) -> int:
    seed = ExperienceSeed(
        name=name,
        section=ExperienceSection.PROJECTS_HACKATHONS,
        kind=ExperienceKind.PROJECT,
        aliases=tuple(aliases),
    )
    return repo.upsert_experience(USER, seed).id


def _truth_roster(repo: InMemoryClaimRepository) -> tuple[int, int, int]:
    """The user-confirmed roster of the truth case: children yes, container no."""
    oneworld = _confirm(repo, "OneWorld Health Platform", "OneWorld", NESTED_OW)
    paper = _confirm(repo, "Paper Recommender System", "paper-recommender", NESTED_PR)
    jobpilot = _confirm(repo, "jobpilot", "cmorreale/jobpilot")
    return oneworld, paper, jobpilot


async def _assign(repo: InMemoryClaimRepository) -> object:
    return await run_roster_assignment(
        EmptyDriveClient(), MockGitHubClient(FIXTURES), USER, repo, _settings()
    )


def _rows_for_base(repo: InMemoryClaimRepository, base_prefix: str, source_type: str):
    rows = []
    for row in repo.list_all_evidence(USER):
        if row.source_type != source_type:
            continue
        base, _ = split_span_ref(row.source_ref)
        if base == base_prefix or base.startswith(base_prefix):
            rows.append(row)
    return rows


# --- 16.2/16.6: the collection's root README sections follow their child projects ------


@pytest.mark.asyncio
async def test_collection_root_readme_sections_assign_to_child_projects() -> None:
    repo = InMemoryClaimRepository()
    oneworld, paper, _ = _truth_roster(repo)
    await _assign(repo)

    rows = _rows_for_base(repo, PORTFOLIO, SOURCE_GITHUB_README)
    assert rows, "portfolio root README produced no evidence"
    by_text = {row.chunk_text: row for row in rows}
    ow_row = next(row for text, row in by_text.items() if "Top 3 out of 100+ teams" in text)
    pr_row = next(row for text, row in by_text.items() if "Restored date coverage" in text)
    assert ow_row.experience_id == oneworld, (
        "OneWorld section evidence must belong to OneWorld, not the container"
    )
    assert pr_row.experience_id == paper, (
        "Paper Recommender section evidence must belong to Paper Recommender"
    )
    # No portfolio project exists to own anything — and nothing was force-assigned
    # by repository reference.
    assert all(row.assignment_method != ASSIGNMENT_README_REF for row in rows), (
        "collection-repo README chunks must not be force-assigned by repo ref"
    )


@pytest.mark.asyncio
async def test_collection_preamble_stays_unassigned() -> None:
    repo = InMemoryClaimRepository()
    _truth_roster(repo)
    await _assign(repo)

    rows = _rows_for_base(repo, PORTFOLIO, SOURCE_GITHUB_README)
    preamble = [row for row in rows if "table of contents" in row.chunk_text]
    assert preamble, "preamble chunk missing"
    assert all(row.experience_id is None for row in preamble), (
        "generic container prose must stay honestly unassigned"
    )


# --- §4.8/7.4: repo-wide commits in a collection stay unresolved/supporting-only -------


@pytest.mark.asyncio
async def test_collection_commits_stay_unassigned() -> None:
    repo = InMemoryClaimRepository()
    _truth_roster(repo)
    await _assign(repo)

    commit_rows = _rows_for_base(repo, f"{PORTFOLIO}@bbb1", SOURCE_GITHUB_COMMIT)
    assert commit_rows, "portfolio commit evidence missing"
    assert all(row.experience_id is None for row in commit_rows), (
        "collection-repo commits must not be force-assigned to any entity"
    )


# --- nested child READMEs belong to their confirmed child project ----------------------


@pytest.mark.asyncio
async def test_nested_readme_assigns_to_confirmed_child_project() -> None:
    repo = InMemoryClaimRepository()
    _, paper, _ = _truth_roster(repo)
    await _assign(repo)

    rows = _rows_for_base(repo, NESTED_PR, SOURCE_GITHUB_DOC)
    assert rows, "nested Paper Recommender README produced no evidence"
    assert {row.experience_id for row in rows} == {paper}


# --- single-project repo: the deterministic user-approved boundary survives ------------


@pytest.mark.asyncio
async def test_single_project_repo_keeps_deterministic_boundary() -> None:
    repo = InMemoryClaimRepository()
    _, _, jobpilot = _truth_roster(repo)
    await _assign(repo)

    readme_rows = _rows_for_base(repo, "cmorreale/jobpilot", SOURCE_GITHUB_README)
    assert readme_rows and {r.experience_id for r in readme_rows} == {jobpilot}
    assert {r.assignment_method for r in readme_rows} == {ASSIGNMENT_README_REF}
    # Repo docs (CLAUDE.md, architecture) of a single-project repo share the boundary.
    doc_rows = _rows_for_base(repo, "cmorreale/jobpilot/CLAUDE.md", SOURCE_GITHUB_DOC)
    assert doc_rows and {r.experience_id for r in doc_rows} == {jobpilot}
    commit_rows = _rows_for_base(repo, "cmorreale/jobpilot@", SOURCE_GITHUB_COMMIT)
    assert commit_rows and {r.experience_id for r in commit_rows} == {jobpilot}
    assert {r.assignment_method for r in commit_rows} == {ASSIGNMENT_REPO_REF}


# --- 16.7: shared alias = ambiguity, never first-match ownership ------------------------


@pytest.mark.asyncio
async def test_shared_repo_alias_is_ambiguity_not_first_match() -> None:
    repo = InMemoryClaimRepository()
    first = _confirm(repo, "JobPilot the product", "cmorreale/jobpilot")
    # Detection dedupes proposals by alias, but a human alias edit (PATCH /roster)
    # can legitimately leave two confirmed entities sharing a repo alias.
    second = _confirm(repo, "JobPilot the thesis writeup")
    repo.update_experience_details(second, aliases=("cmorreale/jobpilot",))
    assert first != second
    report = await _assign(repo)

    readme_rows = _rows_for_base(repo, "cmorreale/jobpilot", SOURCE_GITHUB_README)
    assert readme_rows, "README evidence missing"
    assert all(row.experience_id is None for row in readme_rows), (
        "a shared repo alias must leave evidence unresolved, not first-match assigned"
    )
    commit_rows = _rows_for_base(repo, "cmorreale/jobpilot@", SOURCE_GITHUB_COMMIT)
    assert all(row.experience_id is None for row in commit_rows)
    assert any("cmorreale/jobpilot" in line for line in report.ambiguous), (
        "the ambiguity must be reported, requesting a user decision"
    )


# --- §7.2/§12.4: derived review hints on roster entries --------------------------------


def test_roster_review_hints_flag_containers_and_generic_names() -> None:
    from app.domain.roster import roster_review_hints

    repo = InMemoryClaimRepository()
    container_id = _confirm(repo, "Cameron-Morreale-portfolio", PORTFOLIO)
    child_id = _confirm(repo, "Paper Recommender System", NESTED_PR)
    generic_id = _confirm(repo, "DS4635")
    roster = repo.list_experiences(USER)
    by_id = {e.id: e for e in roster}

    container_hints = roster_review_hints(by_id[container_id], roster)
    assert container_hints["may_be_container"] is True, (
        "a repo with nested child docs must be flagged as a possible container"
    )
    assert container_hints["derived_from_repo_name"] is True

    child_hints = roster_review_hints(by_id[child_id], roster)
    assert child_hints["may_be_container"] is False
    assert child_hints["generic_name"] is False

    generic_hints = roster_review_hints(by_id[generic_id], roster)
    assert generic_hints["generic_name"] is True, (
        "course-code names (DS4635) are not canonical project names without source support"
    )
