"""SQLAlchemy-backed :class:`MasterCvRepository`.

Persists versioned Master CV snapshots to ``master_cv`` and a deduped source registry to
``cv_sources``. Versioning is **idempotent**: saving content whose fingerprint matches the
latest version is a no-op that returns that version.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CvSourceRow, MasterCvRow
from app.domain.cv import CvSource, MasterCv, StoredMasterCv


def _source_hash(source: CvSource) -> str:
    return hashlib.sha256(source.raw_text.encode("utf-8")).hexdigest()


def _to_stored(row: MasterCvRow) -> StoredMasterCv:
    return StoredMasterCv(
        user_id=row.user_id,
        version=row.version,
        content_hash=row.content_hash,
        master_cv=MasterCv.from_content_json(row.content_json),
        created_at=row.created_at,
    )


class SqlMasterCvRepository:
    """Versioned Master CV persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, user_id: str, master_cv: MasterCv) -> StoredMasterCv:
        fingerprint = master_cv.content_fingerprint()
        with self._session_factory() as session:
            latest = self._latest_row(session, user_id)
            if latest is not None and latest.content_hash == fingerprint:
                return _to_stored(latest)  # idempotent: unchanged content

            for source in master_cv.sources:
                self._upsert_source(session, user_id, source)

            next_version = (latest.version + 1) if latest is not None else 1
            content = master_cv.to_content_json()
            content["version"] = next_version
            row = MasterCvRow(
                user_id=user_id,
                version=next_version,
                content_json=content,
                content_hash=fingerprint,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            versioned = MasterCv(
                claims=master_cv.claims, sources=master_cv.sources, version=next_version
            )
            return StoredMasterCv(
                user_id=user_id,
                version=next_version,
                content_hash=fingerprint,
                master_cv=versioned,
                created_at=row.created_at,
            )

    def get_latest(self, user_id: str) -> StoredMasterCv | None:
        with self._session_factory() as session:
            row = self._latest_row(session, user_id)
            return _to_stored(row) if row is not None else None

    def get_version(self, user_id: str, version: int) -> StoredMasterCv | None:
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

    def _latest_row(self, session: Session, user_id: str) -> MasterCvRow | None:
        return session.scalar(
            select(MasterCvRow)
            .where(MasterCvRow.user_id == user_id)
            .order_by(MasterCvRow.version.desc())
            .limit(1)
        )

    def _upsert_source(self, session: Session, user_id: str, source: CvSource) -> None:
        existing = session.scalar(
            select(CvSourceRow).where(
                CvSourceRow.user_id == user_id,
                CvSourceRow.source_type == source.source_type,
                CvSourceRow.external_ref == source.external_ref,
            )
        )
        content_hash = _source_hash(source)
        if existing is None:
            session.add(
                CvSourceRow(
                    user_id=user_id,
                    source_type=source.source_type,
                    external_ref=source.external_ref,
                    title=source.title,
                    mime_type=source.mime_type,
                    raw_text=source.raw_text,
                    content_hash=content_hash,
                    modified_time=source.modified_time,
                    ingested_at=source.ingested_at,
                )
            )
            return
        existing.title = source.title
        existing.mime_type = source.mime_type
        existing.raw_text = source.raw_text
        existing.content_hash = content_hash
        existing.modified_time = source.modified_time
        existing.ingested_at = source.ingested_at
