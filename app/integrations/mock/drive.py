"""Fixture-backed :class:`DriveClient`.

This is the default Drive implementation in dev and tests. It reads a local
``manifest.json`` plus content files under a fixtures directory, so the entire Master CV
ingestion pipeline runs end-to-end with **zero OAuth, zero MCP, and zero network**.

The mock returns *raw* candidates (everything in the manifest). Source policy — folder
scoping and MIME allowlisting — is applied by the service layer, matching how the real
MCP client behaves.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.integrations.base import (
    DriveDocument,
    DriveSource,
    DriveSourceMetadata,
)

_MANIFEST_NAME = "manifest.json"


def _opt_str(value: object) -> str | None:
    """Coerce an optional manifest value (typed ``object``) to ``str | None``."""
    return str(value) if value else None


def _parse_time(value: object) -> datetime | None:
    text = _opt_str(value)
    if text is None:
        return None
    # Fixtures use ISO 8601 with a trailing ``Z``; normalize to +00:00 for fromisoformat.
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class MockDriveClient:
    """Reads career-artifact fixtures from a local directory.

    Args:
        fixtures_dir: directory containing ``manifest.json`` and content files.
    """

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._fixtures_dir = Path(fixtures_dir)
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict[str, object]]:
        manifest_path = self._fixtures_dir / _MANIFEST_NAME
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Mock Drive manifest not found at {manifest_path}. "
                "Set GDRIVE_MOCK_FIXTURES_DIR or add tests/fixtures/drive/manifest.json."
            )
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {entry["id"]: entry for entry in raw["sources"]}

    def _to_source(self, entry: dict[str, object]) -> DriveSource:
        return DriveSource(
            source_ref=str(entry["id"]),
            title=str(entry["name"]),
            mime_type=str(entry["mime_type"]),
            folder_id=_opt_str(entry.get("folder_id")),
            modified_time=_parse_time(entry.get("modified_time")),
        )

    def _entry(self, source_ref: str) -> dict[str, object]:
        try:
            return self._manifest[source_ref]
        except KeyError as exc:
            raise KeyError(f"Unknown mock Drive source_ref: {source_ref!r}") from exc

    async def list_candidate_sources(self, user_id: str) -> list[DriveSource]:
        return [self._to_source(entry) for entry in self._manifest.values()]

    async def read_source(self, source_ref: str) -> DriveDocument:
        entry = self._entry(source_ref)
        content_path = self._fixtures_dir / str(entry["path"])
        text = content_path.read_text(encoding="utf-8")
        return DriveDocument(
            source_ref=source_ref,
            title=str(entry["name"]),
            mime_type=str(entry["mime_type"]),
            text=text,
            folder_id=_opt_str(entry.get("folder_id")),
            modified_time=_parse_time(entry.get("modified_time")),
        )

    async def get_source_metadata(self, source_ref: str) -> DriveSourceMetadata:
        entry = self._entry(source_ref)
        content_path = self._fixtures_dir / str(entry["path"])
        size = content_path.stat().st_size if content_path.exists() else None
        return DriveSourceMetadata(
            source_ref=source_ref,
            title=str(entry["name"]),
            mime_type=str(entry["mime_type"]),
            folder_id=_opt_str(entry.get("folder_id")),
            modified_time=_parse_time(entry.get("modified_time")),
            size_bytes=size,
        )

    async def list_changed_sources(self, user_id: str, since: datetime) -> list[DriveSource]:
        sources = await self.list_candidate_sources(user_id)
        return [s for s in sources if s.modified_time is not None and s.modified_time > since]
