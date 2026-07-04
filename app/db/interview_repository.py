"""SQLAlchemy-backed :class:`InterviewRepository`.

Interviews dedupe on ``(user_id, gmail_message_id)`` so re-scanning the inbox is
idempotent — an existing row is returned untouched (stage never regressed). Prep
packets keep one row per interview with replace semantics.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import InterviewRow, PrepPacketRow
from app.domain.interviews import (
    Interview,
    InterviewInvite,
    InterviewStage,
    PrepPacket,
    validate_interview_transition,
)


def _to_interview(row: InterviewRow) -> Interview:
    return Interview(
        id=row.id,
        user_id=row.user_id,
        gmail_message_id=row.gmail_message_id,
        evidence_quote=row.evidence_quote,
        company=row.company,
        job_title=row.job_title,
        stage=InterviewStage(row.stage),
        received_at=row.received_at,
    )


class SqlInterviewRepository:
    """Interview + prep-packet persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_interview(
        self,
        user_id: str,
        gmail_message_id: str,
        invite: InterviewInvite,
        received_at: datetime | None,
    ) -> tuple[Interview, bool]:
        with self._session_factory() as session:
            row = session.scalar(
                select(InterviewRow).where(
                    InterviewRow.user_id == user_id,
                    InterviewRow.gmail_message_id == gmail_message_id,
                )
            )
            if row is not None:
                return _to_interview(row), False  # stage never regressed
            row = InterviewRow(
                user_id=user_id,
                gmail_message_id=gmail_message_id,
                evidence_quote=invite.evidence_quote,
                company=invite.company,
                job_title=invite.job_title,
                stage=InterviewStage.DETECTED.value,
                received_at=received_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_interview(row), True

    def get_interview(self, interview_id: int) -> Interview | None:
        with self._session_factory() as session:
            row = session.get(InterviewRow, interview_id)
            return _to_interview(row) if row is not None else None

    def list_interviews(self, user_id: str) -> list[Interview]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(InterviewRow)
                .where(InterviewRow.user_id == user_id)
                .order_by(InterviewRow.id)
            )
            return [_to_interview(row) for row in rows]

    def transition_interview(self, interview_id: int, new_stage: InterviewStage) -> Interview:
        with self._session_factory() as session:
            row = session.get(InterviewRow, interview_id)
            if row is None:
                raise LookupError(f"no interview with id {interview_id}")
            validate_interview_transition(InterviewStage(row.stage), new_stage)
            row.stage = new_stage.value
            session.commit()
            session.refresh(row)
            return _to_interview(row)

    def save_prep_packet(self, packet: PrepPacket) -> None:
        with self._session_factory() as session:
            if session.get(InterviewRow, packet.interview_id) is None:
                raise LookupError(f"no interview with id {packet.interview_id}")
            row = session.scalar(
                select(PrepPacketRow).where(PrepPacketRow.interview_id == packet.interview_id)
            )
            if row is None:
                session.add(PrepPacketRow(interview_id=packet.interview_id, content=packet.content))
            else:
                row.content = packet.content
            session.commit()

    def get_prep_packet(self, interview_id: int) -> PrepPacket | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(PrepPacketRow).where(PrepPacketRow.interview_id == interview_id)
            )
            if row is None:
                return None
            return PrepPacket(interview_id=interview_id, content=row.content)
