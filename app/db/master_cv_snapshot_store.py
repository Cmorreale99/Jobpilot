"""SQLAlchemy-backed :class:`MasterCvSnapshotStore` over the ``master_cv`` table.

The Master CV stays canonical as versioned structured JSON in one table. Only V2
snapshot rows — ``content_json.snapshot_of == "approved_claims"`` — are visible to
reads: legacy V1-built rows (the retired ingestion path) may still exist in the table
but can never be served as a Master CV version again. New versions are allocated above
*every* existing row (V1 included) so the ``(user_id, version)`` uniqueness holds.
Idempotent like the V1 repository was: unchanged content creates no new version.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import MasterCvRow
from app.domain.master_cv_snapshot import SNAPSHOT_KIND, StoredSnapshot, snapshot_fingerprint


def _is_snapshot_row(row: MasterCvRow) -> bool:
    return row.content_json.get("snapshot_of") == SNAPSHOT_KIND


def _to_stored(row: MasterCvRow) -> StoredSnapshot:
    return StoredSnapshot(
        user_id=row.user_id,
        version=row.version,
        content=row.content_json,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


class SqlMasterCvSnapshotStore:
    """Versioned snapshot persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, user_id: str, content: dict[str, Any]) -> StoredSnapshot:
        fingerprint = snapshot_fingerprint(content)
        with self._session_factory() as session:
            latest = self._latest_snapshot_row(session, user_id)
            if latest is not None and latest.content_hash == fingerprint:
                return _to_stored(latest)  # idempotent: unchanged content
            max_version = session.scalar(
                select(func.max(MasterCvRow.version)).where(MasterCvRow.user_id == user_id)
            )
            version = (max_version or 0) + 1
            row = MasterCvRow(
                user_id=user_id,
                version=version,
                content_json={**content, "version": version},
                content_hash=fingerprint,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_stored(row)

    def get_latest(self, user_id: str) -> StoredSnapshot | None:
        with self._session_factory() as session:
            row = self._latest_snapshot_row(session, user_id)
            return _to_stored(row) if row is not None else None

    def get_version(self, user_id: str, version: int) -> StoredSnapshot | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(MasterCvRow).where(
                    MasterCvRow.user_id == user_id, MasterCvRow.version == version
                )
            )
            if row is None or not _is_snapshot_row(row):
                return None  # a legacy V1 row is not a servable version
            return _to_stored(row)

    def list_versions(self, user_id: str) -> list[int]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(MasterCvRow)
                .where(MasterCvRow.user_id == user_id)
                .order_by(MasterCvRow.version)
            )
            return [row.version for row in rows if _is_snapshot_row(row)]

    @staticmethod
    def _latest_snapshot_row(session: Session, user_id: str) -> MasterCvRow | None:
        # The JSON filter runs in Python for cross-backend portability (SQLite dev,
        # Postgres prod); per-user version counts stay small.
        rows = session.scalars(
            select(MasterCvRow)
            .where(MasterCvRow.user_id == user_id)
            .order_by(MasterCvRow.version.desc())
        )
        for row in rows:
            if _is_snapshot_row(row):
                return row
        return None
