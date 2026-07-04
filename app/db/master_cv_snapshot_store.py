"""SQLAlchemy-backed :class:`MasterCvSnapshotStore` over the ``master_cv`` table.

The Master CV stays canonical as versioned structured JSON in one table. V2 snapshot
rows are distinguished by ``content_json.snapshot_of == "approved_claims"``; they share
the per-user version sequence with any V1-built rows (the V1 ingestion path is retired
from the nightly once the V2 review loop owns the Master CV — see PLAN/M11).
Idempotent like the V1 repository: unchanged content creates no new version.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import MasterCvRow
from app.domain.master_cv_snapshot import StoredSnapshot, snapshot_fingerprint


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
            latest = self._latest_row(session, user_id)
            if latest is not None and latest.content_hash == fingerprint:
                return _to_stored(latest)  # idempotent: unchanged content
            version = latest.version + 1 if latest is not None else 1
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
            row = self._latest_row(session, user_id)
            return _to_stored(row) if row is not None else None

    def get_version(self, user_id: str, version: int) -> StoredSnapshot | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(MasterCvRow).where(
                    MasterCvRow.user_id == user_id, MasterCvRow.version == version
                )
            )
            return _to_stored(row) if row is not None else None

    def list_versions(self, user_id: str) -> list[int]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(MasterCvRow.version)
                    .where(MasterCvRow.user_id == user_id)
                    .order_by(MasterCvRow.version)
                )
            )

    @staticmethod
    def _latest_row(session: Session, user_id: str) -> MasterCvRow | None:
        return session.scalar(
            select(MasterCvRow)
            .where(MasterCvRow.user_id == user_id)
            .order_by(MasterCvRow.version.desc())
            .limit(1)
        )
