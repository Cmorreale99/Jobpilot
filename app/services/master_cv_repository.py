"""In-memory :class:`MasterCvRepository` for mock-first tests and offline runs."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.cv import MasterCv, StoredMasterCv


class InMemoryMasterCvRepository:
    """Non-persistent, idempotent versioned Master CV store."""

    def __init__(self) -> None:
        self._history: dict[str, list[StoredMasterCv]] = {}

    def save(self, user_id: str, master_cv: MasterCv) -> StoredMasterCv:
        fingerprint = master_cv.content_fingerprint()
        history = self._history.setdefault(user_id, [])
        if history and history[-1].content_hash == fingerprint:
            return history[-1]  # idempotent: unchanged content
        version = (history[-1].version + 1) if history else 1
        stored = StoredMasterCv(
            user_id=user_id,
            version=version,
            content_hash=fingerprint,
            master_cv=MasterCv(claims=master_cv.claims, sources=master_cv.sources, version=version),
            created_at=datetime.now(tz=UTC),
        )
        history.append(stored)
        return stored

    def get_latest(self, user_id: str) -> StoredMasterCv | None:
        history = self._history.get(user_id)
        return history[-1] if history else None

    def get_version(self, user_id: str, version: int) -> StoredMasterCv | None:
        for stored in self._history.get(user_id, []):
            if stored.version == version:
                return stored
        return None

    def list_versions(self, user_id: str) -> list[int]:
        return [stored.version for stored in self._history.get(user_id, [])]
