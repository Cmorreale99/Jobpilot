"""Interview HTTP routes over an injected in-memory repository."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.interviews import InterviewInvite, PrepPacket
from app.main import create_app
from app.services.interview_repository import InMemoryInterviewRepository
from fastapi.testclient import TestClient


@pytest.fixture
def repo() -> InMemoryInterviewRepository:
    return InMemoryInterviewRepository()


@pytest.fixture
def client(repo: InMemoryInterviewRepository) -> TestClient:
    return TestClient(create_app(interview_repository=repo))


def _seed(repo: InMemoryInterviewRepository, *, with_packet: bool = True) -> int:
    interview, _ = repo.upsert_interview(
        "u1",
        "msg-1",
        InterviewInvite(company="Ledgerline", job_title="Staff Backend Engineer"),
        datetime(2026, 6, 30, tzinfo=UTC),
    )
    if with_packet:
        repo.save_prep_packet(PrepPacket(interview_id=interview.id, content="# Prep\nLead with X."))
    return interview.id


def test_list_interviews(client: TestClient, repo: InMemoryInterviewRepository) -> None:
    interview_id = _seed(repo)
    (entry,) = client.get("/interviews", params={"user_id": "u1"}).json()
    assert entry["id"] == interview_id
    assert entry["company"] == "Ledgerline"
    assert entry["stage"] == "detected"
    assert entry["has_prep_packet"] is True
    assert entry["allowed_transitions"] == ["cancelled", "scheduled"]


def test_detail_includes_packet_content(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    body = client.get(f"/interviews/{interview_id}").json()
    assert body["prep_packet"].startswith("# Prep")


def test_detail_without_packet(client: TestClient, repo: InMemoryInterviewRepository) -> None:
    interview_id = _seed(repo, with_packet=False)
    body = client.get(f"/interviews/{interview_id}").json()
    assert body["prep_packet"] is None
    assert body["has_prep_packet"] is False


def test_detail_404(client: TestClient) -> None:
    assert client.get("/interviews/999").status_code == 404


def test_transition_happy_path(client: TestClient, repo: InMemoryInterviewRepository) -> None:
    interview_id = _seed(repo)
    response = client.post(f"/interviews/{interview_id}/transition", json={"stage": "scheduled"})
    assert response.status_code == 200
    assert response.json()["stage"] == "scheduled"


def test_transition_illegal_jump_409(client: TestClient, repo: InMemoryInterviewRepository) -> None:
    interview_id = _seed(repo)
    response = client.post(f"/interviews/{interview_id}/transition", json={"stage": "completed"})
    assert response.status_code == 409


def test_transition_unknown_stage_422(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    response = client.post(f"/interviews/{interview_id}/transition", json={"stage": "ghosted"})
    assert response.status_code == 422


def test_transition_missing_404(client: TestClient) -> None:
    assert client.post("/interviews/999/transition", json={"stage": "scheduled"}).status_code == 404
