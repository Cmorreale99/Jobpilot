"""Master CV snapshot service + the in-memory store.

``create_master_cv_snapshot`` is THE version-snapshot query the outline demands:
it asks the claim repository for ``status=approved`` claims only, so an unapproved
claim cannot enter a Master CV version. (The domain builder filters again as a
defense in depth; the exit test proves both.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.claims import ClaimRepository, ClaimStatus
from app.domain.master_cv_snapshot import (
    MasterCvSnapshotStore,
    StoredSnapshot,
    build_snapshot_content,
    snapshot_fingerprint,
)


def create_master_cv_snapshot(
    user_id: str, repository: ClaimRepository, store: MasterCvSnapshotStore
) -> StoredSnapshot:
    """Snapshot the user's approved claims as a new (or unchanged) Master CV version."""
    experiences = repository.list_experiences(user_id)
    approved = repository.list_claims(user_id, status=ClaimStatus.APPROVED)
    content = build_snapshot_content(experiences, approved)
    return store.save(user_id, content)


class InMemorySnapshotStore(MasterCvSnapshotStore):
    """Dict-backed snapshot store with the same idempotent-versioning semantics."""

    def __init__(self) -> None:
        self._versions: dict[str, list[StoredSnapshot]] = {}

    def save(self, user_id: str, content: dict[str, Any]) -> StoredSnapshot:
        fingerprint = snapshot_fingerprint(content)
        versions = self._versions.setdefault(user_id, [])
        if versions and versions[-1].content_hash == fingerprint:
            return versions[-1]  # idempotent: unchanged content
        version = versions[-1].version + 1 if versions else 1
        stored = StoredSnapshot(
            user_id=user_id,
            version=version,
            content={**content, "version": version},
            content_hash=fingerprint,
            created_at=datetime.now(tz=UTC),
        )
        versions.append(stored)
        return stored

    def get_latest(self, user_id: str) -> StoredSnapshot | None:
        versions = self._versions.get(user_id)
        return versions[-1] if versions else None

    def get_version(self, user_id: str, version: int) -> StoredSnapshot | None:
        for stored in self._versions.get(user_id, []):
            if stored.version == version:
                return stored
        return None

    def list_versions(self, user_id: str) -> list[int]:
        return [stored.version for stored in self._versions.get(user_id, [])]
