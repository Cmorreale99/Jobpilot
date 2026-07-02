"""In-memory :class:`InterviewRepository` for mock-first tests and offline runs."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.domain.interviews import (
    Interview,
    InterviewInvite,
    InterviewStage,
    PrepPacket,
    validate_interview_transition,
)


class InMemoryInterviewRepository:
    """Non-persistent interview + prep-packet store, same semantics as the SQL one."""

    def __init__(self) -> None:
        self._interviews: dict[int, Interview] = {}
        self._by_message: dict[tuple[str, str], int] = {}
        self._packets: dict[int, PrepPacket] = {}
        self._next_id = 1

    def upsert_interview(
        self,
        user_id: str,
        source_message_id: str,
        invite: InterviewInvite,
        received_at: datetime | None,
    ) -> tuple[Interview, bool]:
        key = (user_id, source_message_id)
        existing_id = self._by_message.get(key)
        if existing_id is not None:
            return self._interviews[existing_id], False  # stage never regressed
        interview = Interview(
            id=self._next_id,
            user_id=user_id,
            source_message_id=source_message_id,
            company=invite.company,
            job_title=invite.job_title,
            stage=InterviewStage.DETECTED,
            received_at=received_at,
        )
        self._next_id += 1
        self._interviews[interview.id] = interview
        self._by_message[key] = interview.id
        return interview, True

    def get_interview(self, interview_id: int) -> Interview | None:
        return self._interviews.get(interview_id)

    def list_interviews(self, user_id: str) -> list[Interview]:
        return sorted(
            (i for i in self._interviews.values() if i.user_id == user_id),
            key=lambda i: i.id,
        )

    def transition_interview(self, interview_id: int, new_stage: InterviewStage) -> Interview:
        interview = self._interviews.get(interview_id)
        if interview is None:
            raise LookupError(f"no interview with id {interview_id}")
        validate_interview_transition(interview.stage, new_stage)
        updated = replace(interview, stage=new_stage)
        self._interviews[interview_id] = updated
        return updated

    def save_prep_packet(self, packet: PrepPacket) -> None:
        if packet.interview_id not in self._interviews:
            raise LookupError(f"no interview with id {packet.interview_id}")
        self._packets[packet.interview_id] = packet

    def get_prep_packet(self, interview_id: int) -> PrepPacket | None:
        return self._packets.get(interview_id)
