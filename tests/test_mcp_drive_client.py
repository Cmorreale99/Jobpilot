"""Offline tests for the async MCP-backed Drive client.

These drive McpDriveClient through a fake ClientSession, so the tool-call plumbing and
the response->dataclass mapping are exercised end-to-end without a live MCP server,
network, or OAuth. Only the real transport/auth handshake remains unverified here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.config import Settings
from app.integrations.base import (
    DriveCredentials,
    DriveDocument,
    DriveResponseError,
    DriveSource,
    DriveSourceMetadata,
)
from app.integrations.mcp.drive import DriveToolNames, McpDriveClient

from tests.conftest import APPROVED_FOLDER_ID

TOOLS = DriveToolNames()


class _FakeResult:
    def __init__(self, structured: Any = None, text: str | None = None) -> None:
        self.structuredContent = structured
        self.content = [_TextBlock(text)] if text is not None else []


class _TextBlock:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeSession:
    """Records calls and returns canned payloads keyed by tool name."""

    def __init__(self, responses: dict[str, _FakeResult]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeResult:
        self.calls.append((name, arguments))
        return self._responses[name]


def _mcp_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "gdrive_mcp_enabled": True,
        "gdrive_mcp_server": "http://localhost:9000/mcp",
        "gdrive_source_folder_id": APPROVED_FOLDER_ID,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _client(responses: dict[str, _FakeResult]) -> tuple[McpDriveClient, _FakeSession]:
    session = _FakeSession(responses)

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    client = McpDriveClient(
        _mcp_settings(),
        credentials=DriveCredentials(user_email="jordan@example.com", access_token="tok"),
        session_factory=factory,
    )
    return client, session


async def test_list_candidate_sources_maps_files() -> None:
    payload = {
        "files": [
            {
                "id": "1AbC",
                "name": "Master Resume.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-05-14T09:30:00Z",
                "parents": [APPROVED_FOLDER_ID],
            }
        ]
    }
    client, session = _client({TOOLS.list_folder: _FakeResult(structured=payload)})

    sources = await client.list_candidate_sources("user-1")

    assert sources == [
        DriveSource(
            source_ref="1AbC",
            title="Master Resume.pdf",
            mime_type="application/pdf",
            folder_id=APPROVED_FOLDER_ID,
            modified_time=sources[0].modified_time,
        )
    ]
    assert sources[0].modified_time is not None
    # Folder scope + the user's email are passed to the tool.
    name, args = session.calls[0]
    assert name == TOOLS.list_folder
    assert args["folder_id"] == APPROVED_FOLDER_ID
    assert args["user_google_email"] == "jordan@example.com"


async def test_read_source_maps_content() -> None:
    payload = {"name": "Resume", "mimeType": "text/plain", "content": "Jordan Rivera ..."}
    client, _ = _client({TOOLS.read_content: _FakeResult(structured=payload)})

    doc = await client.read_source("1AbC")

    assert isinstance(doc, DriveDocument)
    assert doc.source_ref == "1AbC"
    assert doc.text == "Jordan Rivera ..."
    assert doc.mime_type == "text/plain"


async def test_get_source_metadata_maps_size() -> None:
    payload = {"name": "Resume", "mimeType": "text/plain", "size": "2048"}
    client, _ = _client({TOOLS.metadata: _FakeResult(structured=payload)})

    meta = await client.get_source_metadata("1AbC")

    assert isinstance(meta, DriveSourceMetadata)
    assert meta.size_bytes == 2048


async def test_list_changed_sources_builds_scoped_query() -> None:
    from datetime import UTC, datetime

    client, session = _client({TOOLS.search: _FakeResult(structured={"files": []})})

    await client.list_changed_sources("user-1", datetime(2026, 6, 1, tzinfo=UTC))

    _, args = session.calls[0]
    assert f"'{APPROVED_FOLDER_ID}' in parents" in args["query"]
    assert "modifiedTime > '2026-06-01" in args["query"]


async def test_extract_payload_parses_json_text_block() -> None:
    # No structuredContent: fall back to a JSON text block.
    payload_text = '{"name": "R", "mimeType": "text/plain", "content": "hi"}'
    client, _ = _client({TOOLS.read_content: _FakeResult(text=payload_text)})

    doc = await client.read_source("1AbC")
    assert doc.text == "hi"


async def test_non_json_text_payload_raises() -> None:
    client, _ = _client({TOOLS.read_content: _FakeResult(text="Filename: R\nnot json")})
    with pytest.raises(DriveResponseError):
        await client.read_source("1AbC")


async def test_pipeline_runs_through_mcp_client() -> None:
    """The evidence gathering is client-agnostic: it works against the MCP client too."""
    from app.integrations.mock.github import MockGitHubClient
    from app.services.roster import gather_source_documents

    from tests.conftest import GITHUB_FIXTURES_DIR

    list_payload = {
        "files": [
            {
                "id": "1AbC",
                "name": "Resume.txt",
                "mimeType": "text/plain",
                "parents": [APPROVED_FOLDER_ID],
            }
        ]
    }
    content_payload = {
        "name": "Resume.txt",
        "mimeType": "text/plain",
        "content": (
            "Problem: Payouts were late.\n"
            "Action: Rebuilt the settlement pipeline.\n"
            "Result: Cut runtime to 40 minutes."
        ),
    }
    client, _ = _client(
        {
            TOOLS.list_folder: _FakeResult(structured=list_payload),
            TOOLS.read_content: _FakeResult(structured=content_payload),
        }
    )

    # No github_username in the settings, so the repo policy admits nothing — the
    # documents come from the MCP Drive client alone.
    documents = await gather_source_documents(
        client, MockGitHubClient(GITHUB_FIXTURES_DIR), "user-1", _mcp_settings()
    )

    (document,) = documents
    assert document.title == "Resume.txt"
    assert document.source_ref == "1AbC"
    assert "Rebuilt the settlement pipeline." in document.text  # normalized, intact


def test_stdio_transport_requires_a_launch_command() -> None:
    from app.integrations.base import DriveConfigurationError

    with pytest.raises(DriveConfigurationError, match="GDRIVE_MCP_COMMAND"):
        McpDriveClient(
            _mcp_settings(gdrive_mcp_transport="stdio", gdrive_mcp_server=""),
            credentials=DriveCredentials(user_email="jordan@example.com", access_token="tok"),
        )


def test_stdio_transport_needs_no_server_url() -> None:
    client = McpDriveClient(
        _mcp_settings(
            gdrive_mcp_transport="stdio",
            gdrive_mcp_server="",
            gdrive_mcp_command="uvx",
            gdrive_mcp_args="workspace-mcp --tools drive",
        ),
        credentials=DriveCredentials(user_email="jordan@example.com", access_token="tok"),
    )
    assert isinstance(client, McpDriveClient)


# --- formatted-text parsing (shapes verified live against workspace-mcp) -----------

_LIVE_LISTING = (
    "Found 2 items in folder '1Mc_FOLDER' for user@example.com:\n"
    '- Name: "resume (1).pdf" (ID: 1RK-file1, Type: application/pdf, Size: 95181, '
    "Created: 2026-07-02T16:26:09.857Z, Modified: 2026-07-01T00:05:16.000Z, "
    "Last Edited By: Cam M <user@example.com>) Link: https://drive.google.com/file/d/1RK-file1/view\n"
    '- Name: "Case Studies (Wellington, LLM Pipelines).pdf" (ID: 1Vz-file2, '
    "Type: application/pdf, Size: 754095, Created: 2026-07-02T16:27:02.625Z, "
    "Modified: 2026-05-14T17:14:28.000Z) Link: https://drive.google.com/file/d/1Vz-file2/view\n"
)

_LIVE_CONTENT = (
    'File: "resume (1).pdf" (ID: 1RK-file1, Type: application/pdf)\n'
    "Link: https://drive.google.com/file/d/1RK-file1/view\n\n"
    "--- CONTENT ---\n"
    "Cam Morreale Reverse-engineers broken data systems into decision infrastructure"
)


async def test_listing_text_is_parsed_into_sources() -> None:
    client, _ = _client({TOOLS.list_folder: _FakeResult(structured={"result": _LIVE_LISTING})})
    sources = await client.list_candidate_sources("u1")
    assert [s.source_ref for s in sources] == ["1RK-file1", "1Vz-file2"]
    first = sources[0]
    assert first.title == "resume (1).pdf"
    assert first.mime_type == "application/pdf"
    assert first.folder_id == "1Mc_FOLDER"
    assert first.modified_time is not None and first.modified_time.year == 2026
    # Titles containing parentheses/commas must not confuse the parser.
    assert sources[1].title == "Case Studies (Wellington, LLM Pipelines).pdf"


async def test_content_text_is_parsed_into_document() -> None:
    client, _ = _client({TOOLS.read_content: _FakeResult(structured={"result": _LIVE_CONTENT})})
    doc = await client.read_source("1RK-file1")
    assert doc.title == "resume (1).pdf"
    assert doc.mime_type == "application/pdf"
    assert doc.text.startswith("Cam Morreale Reverse-engineers")


async def test_error_results_raise_instead_of_parsing_empty() -> None:
    error = _FakeResult(text="Error calling tool 'list_drive_items': boom")
    error.isError = True
    client, _ = _client({TOOLS.list_folder: error})
    with pytest.raises(DriveResponseError, match="returned an error"):
        await client.list_candidate_sources("u1")
