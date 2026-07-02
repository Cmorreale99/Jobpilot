"""MCP-backed :class:`DriveClient` for the Google Workspace MCP server.

Targets the community **Google Workspace MCP** server
(https://github.com/taylorwilsdon/google_workspace_mcp), whose Drive tools are:

* ``search_drive_files``       — query-based search (used for change detection)
* ``list_drive_items``         — list the contents of a folder (candidate discovery)
* ``get_drive_file_content``   — export/read a file's text
* ``get_drive_file_permissions`` — file metadata

Each tool takes a ``user_google_email`` argument, supplied from
:class:`DriveCredentials`. Tool names are grounded in that server's source but kept in
one overridable :class:`DriveToolNames` object, and are validated against the server's
advertised tools on connect (via ``list_tools``) so a server mismatch fails with a clear
message instead of a silent miss.

Design:

* **Async** — the client talks to the MCP server over the network; the ingestion service
  awaits it. The mock implements the same async contract.
* **Injectable session** — ``session_factory`` lets tests drive a fake ``ClientSession``
  offline, so response mapping is fully unit-tested without a live server or credentials.
* **Config-only endpoint** — server URL/transport come from ``Settings``; no URLs are
  hardcoded in logic.

Remaining server-specific work (see TODOs): the Workspace tools return human-formatted
text, not guaranteed JSON. This adapter maps from a tool result's *structured content*
(``structuredContent`` / a JSON ``TextContent`` body). If your server build only returns
formatted text, add a text parser at :meth:`_extract_payload` — that is the single spot
that needs to match your server's serialization.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.integrations.base import (
    DriveConfigurationError,
    DriveCredentials,
    DriveDocument,
    DriveResponseError,
    DriveSource,
    DriveSourceMetadata,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp import ClientSession

# A session factory returns an async context manager yielding an initialized,
# tool-validated MCP session.
SessionFactory = Callable[[], "AbstractAsyncContextManager[ClientSession]"]


@dataclass(frozen=True)
class DriveToolNames:
    """Google Workspace MCP Drive tool names (overridable to match your server)."""

    list_folder: str = "list_drive_items"
    search: str = "search_drive_files"
    read_content: str = "get_drive_file_content"
    metadata: str = "get_drive_file_permissions"

    def all(self) -> tuple[str, ...]:
        return (self.list_folder, self.search, self.read_content, self.metadata)


class McpDriveClient:
    """Adapter from Google Workspace MCP Drive tools to the ``DriveClient`` interface."""

    def __init__(
        self,
        settings: Settings,
        credentials: DriveCredentials | None = None,
        session_factory: SessionFactory | None = None,
        tools: DriveToolNames | None = None,
    ) -> None:
        self._settings = settings
        self._tools = tools or DriveToolNames()
        self._credentials = credentials or _credentials_from_settings(settings)
        self._session_factory = session_factory or self._default_session_factory
        self._validate_config()

    # --- configuration ------------------------------------------------------------

    def _validate_config(self) -> None:
        if not self._settings.gdrive_mcp_enabled:
            raise DriveConfigurationError(
                "McpDriveClient constructed while GDRIVE_MCP_ENABLED is false. "
                "Use the mock client, or set GDRIVE_MCP_ENABLED=true."
            )
        transport = self._settings.gdrive_mcp_transport
        if transport == "http" and not self._settings.gdrive_mcp_server:
            raise DriveConfigurationError(
                "GDRIVE_MCP_ENABLED is true but GDRIVE_MCP_SERVER is empty. "
                "Set GDRIVE_MCP_SERVER to the Google Workspace MCP endpoint."
            )
        if transport == "stdio" and not self._settings.gdrive_mcp_command:
            raise DriveConfigurationError(
                "GDRIVE_MCP_TRANSPORT is stdio but GDRIVE_MCP_COMMAND is empty. "
                "Set GDRIVE_MCP_COMMAND (and GDRIVE_MCP_ARGS) to launch the server."
            )
        if (
            not self._settings.gdrive_source_folder_id
            and not self._settings.gdrive_allow_broad_scan
        ):
            raise DriveConfigurationError(
                "No ingestion scope: set GDRIVE_SOURCE_FOLDER_ID to an approved folder, "
                "or explicitly enable GDRIVE_ALLOW_BROAD_SCAN."
            )

    def _require_credentials(self) -> DriveCredentials:
        if self._credentials is None:
            raise DriveConfigurationError(
                "No Google Drive credentials available. Provide DriveCredentials from the "
                "encrypted oauth_credentials store, or set GDRIVE_MCP_USER_EMAIL and "
                "GDRIVE_MCP_ACCESS_TOKEN for a dev run."
            )
        return self._credentials

    # --- session management -------------------------------------------------------

    @asynccontextmanager
    async def _default_session_factory(self) -> AsyncIterator[ClientSession]:
        # TODO(mcp-auth): pass the OAuth token to the server. For http, most Workspace
        # MCP deployments accept an ``Authorization: Bearer <token>`` header; for stdio
        # the token is provided via the server's own env/config. Wire this to the real
        # deployment once its auth contract is confirmed.
        from mcp import ClientSession

        creds = self._require_credentials()
        transport = self._settings.gdrive_mcp_transport
        if transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            headers = {"Authorization": f"Bearer {creds.access_token}"}
            async with (
                streamablehttp_client(self._settings.gdrive_mcp_server, headers=headers) as (
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
            from mcp.client.stdio import stdio_client

            if not self._settings.gdrive_mcp_command:
                raise DriveConfigurationError(
                    "GDRIVE_MCP_TRANSPORT is stdio but GDRIVE_MCP_COMMAND is empty."
                )
            # A locally launched server reads its auth from the environment; the token
            # never appears on the command line (visible in process listings).
            params = StdioServerParameters(
                command=self._settings.gdrive_mcp_command,
                args=self._settings.gdrive_mcp_args.split(),
                env={
                    "GOOGLE_ACCESS_TOKEN": creds.access_token,
                    "USER_GOOGLE_EMAIL": creds.user_email,
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
            raise DriveConfigurationError(
                f"Unsupported GDRIVE_MCP_TRANSPORT: {transport!r} (expected http/stdio)."
            )

    async def _verify_tools(self, session: ClientSession) -> None:
        """Fail clearly if the server does not advertise the tools we depend on."""
        advertised = {tool.name for tool in (await session.list_tools()).tools}
        missing = [name for name in self._tools.all() if name not in advertised]
        if missing:
            raise DriveConfigurationError(
                f"Google Drive MCP server {self._settings.gdrive_mcp_server!r} is missing "
                f"expected tool(s): {missing}. Adjust DriveToolNames to match the server "
                f"(advertised: {sorted(advertised)})."
            )

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        async with self._session_factory() as session:
            result = await session.call_tool(tool_name, arguments)
        return self._extract_payload(result)

    @staticmethod
    def _extract_payload(result: Any) -> Any:
        """Return structured data from a CallToolResult.

        Prefers ``structuredContent``; otherwise parses the first text block as JSON.
        Raises :class:`DriveResponseError` rather than guessing if neither is available —
        this is where a server that returns only formatted text would need a text parser.
        """
        structured = getattr(result, "structuredContent", None)
        if structured:
            return structured
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise DriveResponseError(
                        "Drive MCP tool returned non-JSON text; add a parser for this "
                        "server's format in McpDriveClient._extract_payload."
                    ) from exc
        raise DriveResponseError("Drive MCP tool returned an empty/unsupported payload.")

    # --- DriveClient interface ----------------------------------------------------

    async def list_candidate_sources(self, user_id: str) -> list[DriveSource]:
        creds = self._require_credentials()
        payload = await self._call(
            self._tools.list_folder,
            {
                "user_google_email": creds.user_email,
                "folder_id": self._settings.gdrive_source_folder_id,
            },
        )
        return [_map_source(item) for item in _as_items(payload)]

    async def read_source(self, source_ref: str) -> DriveDocument:
        creds = self._require_credentials()
        payload = await self._call(
            self._tools.read_content,
            {"user_google_email": creds.user_email, "file_id": source_ref},
        )
        return _map_document(payload, source_ref)

    async def get_source_metadata(self, source_ref: str) -> DriveSourceMetadata:
        creds = self._require_credentials()
        payload = await self._call(
            self._tools.metadata,
            {"user_google_email": creds.user_email, "file_id": source_ref},
        )
        return _map_metadata(payload, source_ref)

    async def list_changed_sources(self, user_id: str, since: datetime) -> list[DriveSource]:
        creds = self._require_credentials()
        folder = self._settings.gdrive_source_folder_id
        query = f"modifiedTime > '{since.isoformat()}' and trashed = false"
        if folder:
            query = f"'{folder}' in parents and {query}"
        payload = await self._call(
            self._tools.search,
            {"user_google_email": creds.user_email, "query": query},
        )
        return [_map_source(item) for item in _as_items(payload)]


# --- payload mapping (server response -> our dataclasses) ------------------------
#
# Field names follow Google Drive's ``files`` resource (id, name, mimeType,
# modifiedTime, parents), which the Workspace MCP tools surface. If your server uses
# different keys, adjust the ``.get(...)`` lookups below.


def _credentials_from_settings(settings: Settings) -> DriveCredentials | None:
    if settings.gdrive_mcp_user_email and settings.gdrive_mcp_access_token:
        return DriveCredentials(
            user_email=settings.gdrive_mcp_user_email,
            access_token=settings.gdrive_mcp_access_token,
        )
    return None


def _as_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("files", payload.get("items", []))
        return list(items) if isinstance(items, list) else []
    if isinstance(payload, list):
        return payload
    raise DriveResponseError(f"Unexpected Drive listing payload type: {type(payload)!r}")


def _parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _first_parent(item: dict[str, Any]) -> str | None:
    parents = item.get("parents")
    if isinstance(parents, list) and parents:
        return str(parents[0])
    folder = item.get("folder_id")
    return str(folder) if folder else None


def _map_source(item: dict[str, Any]) -> DriveSource:
    return DriveSource(
        source_ref=str(item["id"]),
        title=str(item.get("name", "")),
        mime_type=str(item.get("mimeType", "")),
        folder_id=_first_parent(item),
        modified_time=_parse_time(item.get("modifiedTime")),
    )


def _map_document(payload: dict[str, Any], source_ref: str) -> DriveDocument:
    text = payload.get("content", payload.get("body", ""))
    if not isinstance(text, str):
        raise DriveResponseError("Drive file content payload had no text 'content'.")
    return DriveDocument(
        source_ref=source_ref,
        title=str(payload.get("name", "")),
        mime_type=str(payload.get("mimeType", "")),
        text=text,
        folder_id=_first_parent(payload),
        modified_time=_parse_time(payload.get("modifiedTime")),
    )


def _map_metadata(payload: dict[str, Any], source_ref: str) -> DriveSourceMetadata:
    return DriveSourceMetadata(
        source_ref=source_ref,
        title=str(payload.get("name", "")),
        mime_type=str(payload.get("mimeType", "")),
        folder_id=_first_parent(payload),
        modified_time=_parse_time(payload.get("modifiedTime")),
        size_bytes=_opt_int(payload.get("size")),
    )


def _opt_int(value: Any) -> int | None:
    """Coerce a Drive ``size`` value (str/int/None) to ``int | None``."""
    if value in (None, ""):
        return None
    return int(value)
