"""Offline tests for the async MCP-backed GitHub client (fake ClientSession)."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.config import Settings
from app.integrations.base import (
    GitHubConfigurationError,
    GitHubCredentials,
    GitHubDocument,
    GitHubResponseError,
)
from app.integrations.github_factory import create_github_client
from app.integrations.mcp.github import GitHubToolNames, McpGitHubClient
from app.integrations.mock.github import MockGitHubClient

from tests.conftest import GITHUB_FIXTURES_DIR, GITHUB_USERNAME

TOOLS = GitHubToolNames()


class _FakeResult:
    def __init__(
        self, structured: Any = None, text: str | None = None, resource_text: str | None = None
    ) -> None:
        self.structuredContent = structured
        self.content: list[object] = [_TextBlock(text)] if text is not None else []
        if resource_text is not None:
            self.content.append(_ResourceBlock(resource_text))


class _TextBlock:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _ResourceBlock:
    """Mimics an EmbeddedResource carrying TextResourceContents (real-server file reads)."""

    def __init__(self, text: str) -> None:
        self.resource = _TextBlock(text)


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResult]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeResult:
        self.calls.append((name, arguments))
        return self._responses[name]


def _mcp_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "github_mcp_enabled": True,
        "github_mcp_server": "http://localhost:9100/mcp",
        "github_username": GITHUB_USERNAME,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _client(responses: dict[str, _FakeResult]) -> tuple[McpGitHubClient, _FakeSession]:
    session = _FakeSession(responses)

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    client = McpGitHubClient(
        _mcp_settings(),
        credentials=GitHubCredentials(access_token="pat"),
        session_factory=factory,
    )
    return client, session


async def test_list_candidate_repos_maps_and_scopes_query() -> None:
    payload = {
        "items": [
            {
                "full_name": "jordanrivera/fraud-stream",
                "name": "fraud-stream",
                "owner": {"login": "jordanrivera"},
                "language": "Python",
                "fork": False,
                "private": False,
                "stargazers_count": 128,
                "pushed_at": "2026-06-10T12:00:00Z",
            }
        ]
    }
    client, session = _client({TOOLS.search: _FakeResult(structured=payload)})

    repos = await client.list_candidate_repos("user-1")

    assert [r.repo_ref for r in repos] == ["jordanrivera/fraud-stream"]
    assert repos[0].owner == "jordanrivera"
    assert repos[0].stars == 128
    # Query is scoped to the user and excludes forks by default.
    _, args = session.calls[0]
    assert "user:jordanrivera" in args["query"]
    assert "fork:false" in args["query"]


async def test_read_repo_decodes_base64_readme() -> None:
    encoded = base64.b64encode(b"# fraud-stream\nRealtime scoring.").decode()
    payload = {"name": "fraud-stream", "encoding": "base64", "content": encoded}
    client, session = _client({TOOLS.file_contents: _FakeResult(structured=payload)})

    doc = await client.read_repo("jordanrivera/fraud-stream")

    assert isinstance(doc, GitHubDocument)
    assert "Realtime scoring." in doc.text
    _, args = session.calls[0]
    assert args == {"owner": "jordanrivera", "repo": "fraud-stream", "path": "README.md"}


async def test_read_repo_handles_embedded_resource_readme() -> None:
    """The real github-mcp-server (v1.5.0): a status text line, then the file body as an
    embedded TextResourceContents — verified live against the Docker stdio server."""
    result = _FakeResult(
        text="successfully downloaded text file (SHA: 6fd1e0cc)",
        resource_text="# fraud-stream\nRealtime scoring over Kafka.",
    )
    client, _ = _client({TOOLS.file_contents: result})

    doc = await client.read_repo("jordanrivera/fraud-stream")

    assert "Realtime scoring over Kafka." in doc.text
    assert doc.title == "fraud-stream"


async def test_get_repo_metadata_combines_search_and_commits() -> None:
    repo_payload = {
        "items": [
            {
                "name": "payments-ledger",
                "owner": {"login": "jordanrivera"},
                "language": "Go",
                "stargazers_count": 57,
                "forks_count": 6,
            }
        ]
    }
    commits_payload = [{"sha": "a"}, {"sha": "b"}, {"sha": "c"}]
    client, _ = _client(
        {
            TOOLS.search: _FakeResult(structured=repo_payload),
            TOOLS.commits: _FakeResult(structured=commits_payload),
        }
    )

    meta = await client.get_repo_metadata("jordanrivera/payments-ledger")

    assert meta.primary_language == "Go"
    assert meta.stars == 57
    assert meta.forks == 6
    assert meta.commit_count == 3


async def test_plain_text_file_payload_becomes_document_text() -> None:
    # Non-JSON text from a file read is the file body itself, not an error.
    client, _ = _client({TOOLS.file_contents: _FakeResult(text="# readme as plain text")})
    doc = await client.read_repo("jordanrivera/fraud-stream")
    assert doc.text == "# readme as plain text"


async def test_non_json_text_listing_raises() -> None:
    client, _ = _client({TOOLS.search: _FakeResult(text="not a listing")})
    with pytest.raises(GitHubResponseError):
        await client.list_candidate_repos("jordanrivera")


async def test_invalid_repo_ref_raises() -> None:
    client, _ = _client({TOOLS.file_contents: _FakeResult(structured={})})
    with pytest.raises(GitHubResponseError):
        await client.read_repo("no-slash")


# --- factory / config guardrails -------------------------------------------------


def test_factory_returns_mock_when_disabled() -> None:
    settings = Settings(
        github_mcp_enabled=False,
        github_username=GITHUB_USERNAME,
        github_mock_fixtures_dir=str(GITHUB_FIXTURES_DIR),
    )
    assert isinstance(create_github_client(settings), MockGitHubClient)


def test_factory_returns_mcp_client_when_enabled() -> None:
    assert isinstance(create_github_client(_mcp_settings()), McpGitHubClient)


def test_mcp_client_requires_server_endpoint() -> None:
    with pytest.raises(GitHubConfigurationError):
        McpGitHubClient(_mcp_settings(github_mcp_server=""))


def test_mcp_client_requires_a_scope() -> None:
    with pytest.raises(GitHubConfigurationError):
        McpGitHubClient(_mcp_settings(github_username="", github_allow_broad_scan=False))


async def test_mcp_client_without_credentials_fails_clearly() -> None:
    client = McpGitHubClient(_mcp_settings())  # no token in settings, none injected
    with pytest.raises(GitHubConfigurationError):
        await client.read_repo("jordanrivera/fraud-stream")


def test_stdio_transport_requires_a_launch_command() -> None:
    with pytest.raises(GitHubConfigurationError, match="GITHUB_MCP_COMMAND"):
        McpGitHubClient(_mcp_settings(github_mcp_transport="stdio", github_mcp_server=""))


def test_stdio_transport_needs_no_server_url() -> None:
    client = McpGitHubClient(
        _mcp_settings(
            github_mcp_transport="stdio",
            github_mcp_server="",
            github_mcp_command="github-mcp-server",
        )
    )
    assert isinstance(client, McpGitHubClient)


async def test_search_owner_falls_back_to_full_name_prefix() -> None:
    """The real server's search results omit owner.login (verified live); the owner
    scope policy must still see the right owner from the full_name prefix."""
    payload = {"items": [{"full_name": "jordanrivera/fraud-stream", "name": "fraud-stream"}]}
    client, _ = _client({TOOLS.search: _FakeResult(structured=payload)})
    (repo,) = await client.list_candidate_repos("jordanrivera")
    assert repo.owner == "jordanrivera"


async def test_error_results_raise_clearly() -> None:
    error = _FakeResult(text="Failed to get file contents.")
    error.isError = True
    client, _ = _client({TOOLS.file_contents: error})
    with pytest.raises(GitHubResponseError, match="returned an error"):
        await client.read_repo("jordanrivera/fraud-stream")
