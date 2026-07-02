"""GitHub repo policy: owner scope, forks/private excluded by default."""

from __future__ import annotations

from app.config import Settings
from app.integrations.mock.github import MockGitHubClient
from app.services.source_policy import apply_repo_policy


async def test_default_policy_keeps_only_owned_original_public_repos(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    raw = await mock_github_client.list_candidate_repos("user-1")
    refs = {r.repo_ref for r in apply_repo_policy(raw, settings)}
    assert refs == {"jordanrivera/fraud-stream", "jordanrivera/payments-ledger"}


async def test_forks_excluded_by_default(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    raw = await mock_github_client.list_candidate_repos("user-1")
    refs = {r.repo_ref for r in apply_repo_policy(raw, settings)}
    assert "jordanrivera/awesome-lists-fork" not in refs


async def test_private_excluded_by_default(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    raw = await mock_github_client.list_candidate_repos("user-1")
    refs = {r.repo_ref for r in apply_repo_policy(raw, settings)}
    assert "jordanrivera/private-scratch" not in refs


async def test_other_owner_excluded_and_not_broad(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    raw = await mock_github_client.list_candidate_repos("user-1")
    refs = {r.repo_ref for r in apply_repo_policy(raw, settings)}
    assert "acme-corp/internal-tool" not in refs


async def test_opt_in_flags_include_forks_and_private(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    opted = settings.model_copy(
        update={"github_include_forks": True, "github_include_private": True}
    )
    raw = await mock_github_client.list_candidate_repos("user-1")
    refs = {r.repo_ref for r in apply_repo_policy(raw, opted)}
    assert "jordanrivera/awesome-lists-fork" in refs
    assert "jordanrivera/private-scratch" in refs
    # Still scoped to the user — the other-owner repo stays out.
    assert "acme-corp/internal-tool" not in refs


async def test_no_scope_configured_ingests_nothing(
    mock_github_client: MockGitHubClient, settings: Settings
) -> None:
    unscoped = settings.model_copy(update={"github_username": "", "github_allow_broad_scan": False})
    raw = await mock_github_client.list_candidate_repos("user-1")
    assert apply_repo_policy(raw, unscoped) == []
