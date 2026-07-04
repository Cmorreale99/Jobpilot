"""SQLAlchemy-backed :class:`ArtifactStore` over the ``artifacts`` table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ArtifactRow
from app.domain.artifacts import Artifact, ArtifactStore


def _to_artifact(row: ArtifactRow) -> Artifact:
    return Artifact(
        id=row.id,
        user_id=row.user_id,
        kind=row.kind,
        file_path=row.file_path,
        master_cv_version=row.master_cv_version,
        created_at=row.created_at,
    )


class SqlArtifactStore(ArtifactStore):
    """Artifact-row persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        user_id: str,
        kind: str,
        file_path: str,
        *,
        master_cv_version: int | None = None,
    ) -> Artifact:
        with self._session_factory() as session:
            row = session.scalar(
                select(ArtifactRow).where(
                    ArtifactRow.user_id == user_id,
                    ArtifactRow.kind == kind,
                    ArtifactRow.master_cv_version == master_cv_version,
                )
            )
            if row is None:
                row = ArtifactRow(
                    user_id=user_id,
                    kind=kind,
                    file_path=file_path,
                    master_cv_version=master_cv_version,
                )
                session.add(row)
            else:
                row.file_path = file_path  # re-render refreshes, never duplicates
            session.commit()
            session.refresh(row)
            return _to_artifact(row)

    def get_latest(self, user_id: str, kind: str) -> Artifact | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ArtifactRow)
                .where(ArtifactRow.user_id == user_id, ArtifactRow.kind == kind)
                .order_by(ArtifactRow.master_cv_version.desc().nulls_last(), ArtifactRow.id.desc())
                .limit(1)
            )
            return _to_artifact(row) if row is not None else None

    def list_artifacts(self, user_id: str, kind: str | None = None) -> list[Artifact]:
        with self._session_factory() as session:
            query = (
                select(ArtifactRow).where(ArtifactRow.user_id == user_id).order_by(ArtifactRow.id)
            )
            if kind is not None:
                query = query.where(ArtifactRow.kind == kind)
            return [_to_artifact(row) for row in session.scalars(query)]
