"""Interview repositories: dedupe, stage preservation, packet replace (memory + SQL)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from app.db.interview_repository import SqlInterviewRepository
from app.db.session import create_all, create_session_factory
from app.domain.applications import InvalidTransitionError
from app.domain.interviews import (
    InterviewInvite,
    InterviewRepository,
    InterviewStage,
    PrepPacket,
)
from app.services.interview_repository import InMemoryInterviewRepository

_INVITE = InterviewInvite(company="Ledgerline", job_title="Staff Backend Engineer")
_RECEIVED = datetime(2026, 6, 30, tzinfo=UTC)


@pytest.fixture(params=["memory", "sql"])
def repo(request: pytest.FixtureRequest) -> Iterator[InterviewRepository]:
    if request.param == "memory":
        yield InMemoryInterviewRepository()
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
        create_all(engine)
        yield SqlInterviewRepository(create_session_factory(engine))


def test_upsert_creates_detected_interview(repo: InterviewRepository) -> None:
    interview, created = repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    assert created is True
    assert interview.stage is InterviewStage.DETECTED
    assert interview.company == "Ledgerline"
    assert repo.get_interview(interview.id) == interview


def test_upsert_is_idempotent_per_message(repo: InterviewRepository) -> None:
    first, _ = repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    again, created = repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    assert created is False
    assert again.id == first.id
    assert len(repo.list_interviews("u1")) == 1


def test_rescan_never_regresses_stage(repo: InterviewRepository) -> None:
    interview, _ = repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    repo.transition_interview(interview.id, InterviewStage.SCHEDULED)
    again, created = repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    assert created is False
    assert again.stage is InterviewStage.SCHEDULED


def test_transition_validates_state_machine(repo: InterviewRepository) -> None:
    interview, _ = repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    with pytest.raises(InvalidTransitionError):
        repo.transition_interview(interview.id, InterviewStage.COMPLETED)
    scheduled = repo.transition_interview(interview.id, InterviewStage.SCHEDULED)
    assert scheduled.stage is InterviewStage.SCHEDULED


def test_transition_missing_interview_raises(repo: InterviewRepository) -> None:
    with pytest.raises(LookupError):
        repo.transition_interview(999, InterviewStage.SCHEDULED)


def test_prep_packet_round_trip_and_replace(repo: InterviewRepository) -> None:
    interview, _ = repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    assert repo.get_prep_packet(interview.id) is None
    repo.save_prep_packet(PrepPacket(interview_id=interview.id, content="v1"))
    repo.save_prep_packet(PrepPacket(interview_id=interview.id, content="v2"))
    packet = repo.get_prep_packet(interview.id)
    assert packet is not None and packet.content == "v2"


def test_prep_packet_for_missing_interview_raises(repo: InterviewRepository) -> None:
    with pytest.raises(LookupError):
        repo.save_prep_packet(PrepPacket(interview_id=999, content="x"))


def test_interviews_are_per_user(repo: InterviewRepository) -> None:
    repo.upsert_interview("u1", "msg-1", _INVITE, _RECEIVED)
    repo.upsert_interview("u2", "msg-1", _INVITE, _RECEIVED)
    assert len(repo.list_interviews("u1")) == 1
    assert len(repo.list_interviews("u2")) == 1
