"""MCP-backed :class:`GitHubClient` for the official GitHub MCP server.

Targets github/github-mcp-server, mapping the narrow ``GitHubClient`` contract onto its
read tools:

* ``search_repositories``  — list the user's repos / detect recent pushes
* ``get_file_contents``    — read a repo's README
* ``list_commits``         — a contribution signal (commit count)

Auth is a personal access token (:class:`GitHubCredentials`), forwarded to the server.
Tool names are grounded in that server but kept in one overridable :class:`GitHubToolNames`
object and validated against the server's advertised tools on connect. Same design as the
Drive client: async, config-only endpoint, injectable session for offline tests.

Remaining server-specific work (see TODOs): a full per-repo *languages* breakdown has no
first-class read tool, so metadata reports the primary language only; and if your server
build returns formatted text rather than structured content, add a parser at
:meth:`_extract_payload`.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.integrations.base import (
    GitHubCommit,
    GitHubConfigurationError,
    GitHubCredentials,
    GitHubDocument,
    GitHubRepo,
    GitHubRepoMetadata,
    GitHubResponseError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp import ClientSession

SessionFactory = Callable[[], "AbstractAsyncContextManager[ClientSession]"]

_README_PATH = "README.md"


@dataclass(frozen=True)
class GitHubToolNames:
    """GitHub MCP read-tool names (overridable to match your server)."""

    search: str = "search_repositories"
    file_contents: str = "get_file_contents"
    commits: str = "list_commits"

    def all(self) -> tuple[str, ...]:
        return (self.search, self.file_contents, self.commits)


class McpGitHubClient:
    """Adapter from GitHub MCP read tools to the ``GitHubClient`` interface."""

    def __init__(
        self,
        settings: Settings,
        credentials: GitHubCredentials | None = None,
        session_factory: SessionFactory | None = None,
        tools: GitHubToolNames | None = None,
    ) -> None:
        self._settings = settings
        self._tools = tools or GitHubToolNames()
        self._credentials = credentials or _credentials_from_settings(settings)
        self._session_factory = session_factory or self._default_session_factory
        self._validate_config()

    # --- configuration ------------------------------------------------------------

    def _validate_config(self) -> None:
        if not self._settings.github_mcp_enabled:
            raise GitHubConfigurationError(
                "McpGitHubClient constructed while GITHUB_MCP_ENABLED is false. "
                "Use the mock client, or set GITHUB_MCP_ENABLED=true."
            )
        transport = self._settings.github_mcp_transport
        if transport == "http" and not self._settings.github_mcp_server:
            raise GitHubConfigurationError(
                "GITHUB_MCP_ENABLED is true but GITHUB_MCP_SERVER is empty. "
                "Set GITHUB_MCP_SERVER to the GitHub MCP endpoint."
            )
        if transport == "stdio" and not self._settings.github_mcp_command:
            raise GitHubConfigurationError(
                "GITHUB_MCP_TRANSPORT is stdio but GITHUB_MCP_COMMAND is empty. "
                "Set GITHUB_MCP_COMMAND (and GITHUB_MCP_ARGS) to launch the server."
            )
        if not self._settings.github_username and not self._settings.github_allow_broad_scan:
            raise GitHubConfigurationError(
                "No ingestion scope: set GITHUB_USERNAME to the account to ingest, "
                "or explicitly enable GITHUB_ALLOW_BROAD_SCAN."
            )

    def _require_credentials(self) -> GitHubCredentials:
        if self._credentials is None:
            raise GitHubConfigurationError(
                "No GitHub credentials available. Provide GitHubCredentials from the "
                "encrypted oauth_credentials store, or set GITHUB_MCP_ACCESS_TOKEN for a "
                "dev run."
            )
        return self._credentials

    # --- session management -------------------------------------------------------

    @asynccontextmanager
    async def _default_session_factory(self) -> AsyncIterator[ClientSession]:
        # TODO(mcp-auth): confirm the server's auth contract. github/github-mcp-server
        # (remote) accepts an ``Authorization: Bearer <PAT>`` header; a locally launched
        # server instead reads GITHUB_PERSONAL_ACCESS_TOKEN from its own env.
        from mcp import ClientSession

        creds = self._require_credentials()
        transport = self._settings.github_mcp_transport
        if transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            headers = {"Authorization": f"Bearer {creds.access_token}"}
            async with (
                streamablehttp_client(self._settings.github_mcp_server, headers=headers) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                await self._verify_tools(session)
                yield session
        elif transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import get_default_environment, stdio_client

            if not self._settings.github_mcp_command:
                raise GitHubConfigurationError(
                    "GITHUB_MCP_TRANSPORT is stdio but GITHUB_MCP_COMMAND is empty."
                )
            # github/github-mcp-server reads its PAT from the environment when launched
            # locally; the token never appears on the command line. Merge with the SDK
            # defaults — a bare env would strip PATH/SystemRoot and break launchers
            # like docker.
            params = StdioServerParameters(
                command=self._settings.github_mcp_command,
                args=self._settings.github_mcp_args.split(),
                env={
                    **get_default_environment(),
                    "GITHUB_PERSONAL_ACCESS_TOKEN": creds.access_token,
                },
            )
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                await self._verify_tools(session)
                yield session
        else:
            raise GitHubConfigurationError(
                f"Unsupported GITHUB_MCP_TRANSPORT: {transport!r} (expected http/stdio)."
            )

    async def _verify_tools(self, session: ClientSession) -> None:
        advertised = {tool.name for tool in (await session.list_tools()).tools}
        missing = [name for name in self._tools.all() if name not in advertised]
        if missing:
            raise GitHubConfigurationError(
                f"GitHub MCP server {self._settings.github_mcp_server!r} is missing expected "
                f"tool(s): {missing}. Adjust GitHubToolNames to match the server "
                f"(advertised: {sorted(advertised)})."
            )

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        async with self._session_factory() as session:
            result = await session.call_tool(tool_name, arguments)
        if getattr(result, "isError", False):
            detail = ""
            for block in getattr(result, "content", []) or []:
                detail = getattr(block, "text", "") or detail
            raise GitHubResponseError(
                f"GitHub MCP tool {tool_name!r} returned an error: {detail[:500]}"
            )
        return self._extract_payload(result)

    @staticmethod
    def _extract_payload(result: Any) -> Any:
        """Return data from a CallToolResult.

        Precedence, verified against github/github-mcp-server v1.5.0:

        1. ``structuredContent`` (structured tool results).
        2. An ``EmbeddedResource`` text body → returned as a plain ``str``. This is how
           the real server delivers file contents: a human status line in the first
           text block ("successfully downloaded text file (SHA: …)") followed by the
           file body as a ``TextResourceContents`` resource.
        3. The first JSON-parsable text block (REST-passthrough style responses).
        """
        structured = getattr(result, "structuredContent", None)
        if structured:
            # FastMCP-style servers wrap plain-text tool returns as {"result": "<text>"}.
            if (
                isinstance(structured, dict)
                and set(structured) == {"result"}
                and isinstance(structured["result"], str)
            ):
                return structured["result"]
            return structured
        blocks = list(getattr(result, "content", []) or [])
        for block in blocks:
            resource = getattr(block, "resource", None)
            resource_text = getattr(resource, "text", None)
            if isinstance(resource_text, str):
                return resource_text
        plain_text: str | None = None
        for block in blocks:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    plain_text = plain_text or text  # may be the file body itself
        if plain_text is not None:
            return plain_text  # listing paths reject str payloads in _as_items
        raise GitHubResponseError("GitHub MCP tool returned an empty/unsupported payload.")

    # --- GitHubClient interface ---------------------------------------------------

    def _scope_query(self, extra: str = "") -> str:
        parts = []
        if self._settings.github_username:
            parts.append(f"user:{self._settings.github_username}")
        if not self._settings.github_include_forks:
            parts.append("fork:false")
        if extra:
            parts.append(extra)
        return " ".join(parts)

    async def list_candidate_repos(self, user_id: str) -> list[GitHubRepo]:
        self._require_credentials()
        payload = await self._call(self._tools.search, {"query": self._scope_query()})
        return [_map_repo(item) for item in _as_items(payload)]

    async def read_repo(self, repo_ref: str) -> GitHubDocument:
        self._require_credentials()
        owner, repo = _split_ref(repo_ref)
        payload = await self._call(
            self._tools.file_contents,
            {"owner": owner, "repo": repo, "path": _README_PATH},
        )
        if isinstance(payload, str):
            # Real-server shape: the file body arrives as embedded-resource text.
            return GitHubDocument(repo_ref=repo_ref, title=repo, text=payload)
        return _map_document(payload, repo_ref, repo)

    async def list_commits(self, repo_ref: str) -> list[GitHubCommit]:
        self._require_credentials()
        owner, repo = _split_ref(repo_ref)
        payload = await self._call(self._tools.commits, {"owner": owner, "repo": repo})
        return [_map_commit(item, repo_ref) for item in _as_commit_items(payload)]

    async def get_repo_metadata(self, repo_ref: str) -> GitHubRepoMetadata:
        self._require_credentials()
        owner, repo = _split_ref(repo_ref)
        repo_payload = await self._call(self._tools.search, {"query": f"repo:{repo_ref}"})
        commits_payload = await self._call(self._tools.commits, {"owner": owner, "repo": repo})
        item = next(iter(_as_items(repo_payload)), {})
        return _map_metadata(item, repo_ref, repo, owner, _count(commits_payload))

    async def list_changed_repos(self, user_id: str, since: datetime) -> list[GitHubRepo]:
        self._require_credentials()
        query = self._scope_query(f"pushed:>={since.date().isoformat()}")
        payload = await self._call(self._tools.search, {"query": query})
        return [_map_repo(item) for item in _as_items(payload)]


# --- payload mapping (GitHub REST passthrough -> our dataclasses) ----------------


def _credentials_from_settings(settings: Settings) -> GitHubCredentials | None:
    if settings.github_mcp_access_token:
        return GitHubCredentials(access_token=settings.github_mcp_access_token)
    return None


def _split_ref(repo_ref: str) -> tuple[str, str]:
    owner, _, name = repo_ref.partition("/")
    if not owner or not name:
        raise GitHubResponseError(f"Invalid repo_ref (expected 'owner/name'): {repo_ref!r}")
    return owner, name


def _as_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("items", payload.get("repositories", []))
        return list(items) if isinstance(items, list) else []
    if isinstance(payload, list):
        return payload
    raise GitHubResponseError(f"Unexpected GitHub listing payload type: {type(payload)!r}")


def _as_commit_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        commits = payload.get("commits", payload.get("items", []))
        if isinstance(commits, list):
            return [item for item in commits if isinstance(item, dict)]
    raise GitHubResponseError(f"Unexpected GitHub commits payload type: {type(payload)!r}")


def _map_commit(item: dict[str, Any], repo_ref: str) -> GitHubCommit:
    """Map a REST-passthrough commit object ({sha, commit: {message, author: {date}}})."""
    raw_commit = item.get("commit")
    commit: dict[str, Any] = raw_commit if isinstance(raw_commit, dict) else {}
    raw_author = commit.get("author")
    author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
    return GitHubCommit(
        repo_ref=repo_ref,
        sha=str(item.get("sha", "")),
        message=str(commit.get("message", item.get("message", ""))),
        authored_at=_parse_time(author.get("date")),
    )


def _count(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        commits = payload.get("commits", payload.get("items"))
        if isinstance(commits, list):
            return len(commits)
    return None


def _parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _owner_login(item: dict[str, Any]) -> str:
    owner = item.get("owner")
    if isinstance(owner, dict):
        return str(owner.get("login", ""))
    return str(owner) if owner else ""


def _map_repo(item: dict[str, Any]) -> GitHubRepo:
    full_name = str(item.get("full_name", item.get("name", "")))
    # The real server's search results omit owner.login; fall back to the full_name
    # prefix ("owner/repo") so the owner-scope policy still applies (verified live).
    owner = _owner_login(item) or (full_name.partition("/")[0] if "/" in full_name else "")
    return GitHubRepo(
        repo_ref=full_name,
        name=str(item.get("name", full_name.split("/")[-1])),
        owner=owner,
        description=item.get("description"),
        primary_language=item.get("language"),
        is_fork=bool(item.get("fork", False)),
        is_private=bool(item.get("private", False)),
        stars=int(item.get("stargazers_count", 0)),
        pushed_at=_parse_time(item.get("pushed_at")),
    )


def _map_document(payload: dict[str, Any], repo_ref: str, repo_name: str) -> GitHubDocument:
    text = _decode_content(payload)
    return GitHubDocument(
        repo_ref=repo_ref,
        title=str(payload.get("name", repo_name)),
        text=text,
        primary_language=payload.get("language"),
        pushed_at=_parse_time(payload.get("pushed_at")),
    )


def _decode_content(payload: dict[str, Any]) -> str:
    """Extract README text, decoding base64 as GitHub's contents API returns it."""
    raw = payload.get("content", payload.get("text", ""))
    if not isinstance(raw, str):
        raise GitHubResponseError("GitHub file-content payload had no text 'content'.")
    if payload.get("encoding") == "base64":
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    return raw


def _map_metadata(
    item: dict[str, Any],
    repo_ref: str,
    repo_name: str,
    owner: str,
    commit_count: int | None,
) -> GitHubRepoMetadata:
    language = item.get("language")
    return GitHubRepoMetadata(
        repo_ref=repo_ref,
        name=str(item.get("name", repo_name)),
        owner=_owner_login(item) or owner,
        primary_language=language,
        # TODO(mcp): no first-class per-repo languages tool; report the primary language
        # only. Wire a /languages equivalent if the server exposes one.
        languages=(str(language),) if language else (),
        stars=int(item.get("stargazers_count", 0)),
        forks=int(item.get("forks_count", 0)),
        commit_count=commit_count,
        pushed_at=_parse_time(item.get("pushed_at")),
    )
