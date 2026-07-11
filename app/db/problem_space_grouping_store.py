"""SQLAlchemy-backed :class:`GroupingStore` (``problem_space_groupings``, migration 0015).

Same semantics as the in-memory store: keyed by ``(user_id, fingerprint)``, first
write wins — a concurrent duplicate insert loses quietly to the existing row.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ProblemSpaceGroupingRow


class SqlGroupingStore:
    """Recorded-partition persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, user_id: str, fingerprint: str) -> list[list[str]] | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ProblemSpaceGroupingRow).where(
                    ProblemSpaceGroupingRow.user_id == user_id,
                    ProblemSpaceGroupingRow.fingerprint == fingerprint,
                )
            )
            if row is None:
                return None
            return [list(group) for group in (row.groups_json or [])]

    def put(self, user_id: str, fingerprint: str, groups: Sequence[Sequence[str]]) -> None:
        with self._session_factory() as session:
            existing = session.scalar(
                select(ProblemSpaceGroupingRow).where(
                    ProblemSpaceGroupingRow.user_id == user_id,
                    ProblemSpaceGroupingRow.fingerprint == fingerprint,
                )
            )
            if existing is not None:
                return  # first write wins
            session.add(
                ProblemSpaceGroupingRow(
                    user_id=user_id,
                    fingerprint=fingerprint,
                    groups_json=[list(group) for group in groups],
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()  # a concurrent writer recorded it first — keep theirs
