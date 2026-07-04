"""SQLAlchemy-backed :class:`ValidationRunLog` over the ``validation_runs`` table."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ValidationRunRow
from app.domain.validation_runs import ValidationRun, ValidationRunLog


def _to_run(row: ValidationRunRow) -> ValidationRun:
    return ValidationRun(
        id=row.id,
        user_id=row.user_id,
        kind=row.kind,
        subject_ref=row.subject_ref,
        passed=row.passed,
        detail=tuple(row.detail or []),
        created_at=row.created_at,
    )


class SqlValidationRunLog(ValidationRunLog):
    """Append-only validation-run persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        user_id: str,
        kind: str,
        subject_ref: str,
        *,
        passed: bool,
        detail: Sequence[str] = (),
    ) -> ValidationRun:
        with self._session_factory() as session:
            row = ValidationRunRow(
                user_id=user_id,
                kind=kind,
                subject_ref=subject_ref,
                passed=passed,
                detail=list(detail),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_run(row)

    def list_runs(self, user_id: str, kind: str | None = None) -> list[ValidationRun]:
        with self._session_factory() as session:
            query = (
                select(ValidationRunRow)
                .where(ValidationRunRow.user_id == user_id)
                .order_by(ValidationRunRow.id)
            )
            if kind is not None:
                query = query.where(ValidationRunRow.kind == kind)
            return [_to_run(row) for row in session.scalars(query)]
