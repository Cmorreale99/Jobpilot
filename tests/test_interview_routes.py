"""Interview HTTP routes over injected in-memory repositories.

V2: the confirm queue endpoint, the confirm action (which generates the prep packet),
and the no-shortcut state machine (detected can only be confirmed or cancelled).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.interviews import InterviewInvite, InterviewStage, PrepPacket
from app.main import create_app
from app.services.application_repository import InMemoryApplicationRepository
from app.services.interview_repository import InMemoryInterviewRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from fastapi.testclient import TestClient

_QUOTE = "We'd love to set up a 30-minute phone screen with the hiring manager."


@pytest.fixture
def repo() -> InMemoryInterviewRepository:
    return InMemoryInterviewRepository()


@pytest.fixture
def client(repo: InMemoryInterviewRepository) -> TestClient:
    return TestClient(
        create_app(
            interview_repository=repo,
            snapshot_store=InMemorySnapshotStore(),
            application_repository=InMemoryApplicationRepository(),
        )
    )


def _seed(repo: InMemoryInterviewRepository, *, with_packet: bool = False) -> int:
    interview, _ = repo.upsert_interview(
        "u1",
        "msg-1",
        InterviewInvite(
            company="Ledgerline", evidence_quote=_QUOTE, job_title="Staff Backend Engineer"
        ),
        datetime(2026, 6, 30, tzinfo=UTC),
    )
    if with_packet:
        repo.save_prep_packet(PrepPacket(interview_id=interview.id, content="# Prep\nLead with X."))
    return interview.id


def test_list_interviews_carries_provenance(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    (entry,) = client.get("/interviews", params={"user_id": "u1"}).json()
    assert entry["id"] == interview_id
    assert entry["company"] == "Ledgerline"
    assert entry["stage"] == "detected"
    assert entry["gmail_message_id"] == "msg-1"
    assert entry["evidence_quote"] == _QUOTE
    assert entry["has_prep_packet"] is False
    # No detected -> scheduled shortcut: confirm or cancel only.
    assert entry["allowed_transitions"] == ["cancelled", "confirmed"]


def test_confirm_queue_lists_detected_only(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    queue = client.get("/interviews/queue", params={"user_id": "u1"}).json()
    assert [entry["id"] for entry in queue] == [interview_id]

    repo.transition_interview(interview_id, InterviewStage.CANCELLED)
    assert client.get("/interviews/queue", params={"user_id": "u1"}).json() == []


def test_confirm_transitions_and_generates_packet(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    response = client.post(f"/interviews/{interview_id}/confirm")
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "confirmed"
    assert body["has_prep_packet"] is True  # packet exists exactly once confirmed
    packet = repo.get_prep_packet(interview_id)
    assert packet is not None and "Ledgerline" in packet.content


def test_confirm_twice_conflicts(client: TestClient, repo: InMemoryInterviewRepository) -> None:
    interview_id = _seed(repo)
    assert client.post(f"/interviews/{interview_id}/confirm").status_code == 200
    assert client.post(f"/interviews/{interview_id}/confirm").status_code == 409


def test_confirm_missing_404(client: TestClient) -> None:
    assert client.post("/interviews/999/confirm").status_code == 404


def test_detail_includes_packet_content(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo, with_packet=True)
    body = client.get(f"/interviews/{interview_id}").json()
    assert body["prep_packet"].startswith("# Prep")


def test_detail_without_packet(client: TestClient, repo: InMemoryInterviewRepository) -> None:
    interview_id = _seed(repo)
    body = client.get(f"/interviews/{interview_id}").json()
    assert body["prep_packet"] is None
    assert body["has_prep_packet"] is False


def test_detail_404(client: TestClient) -> None:
    assert client.get("/interviews/999").status_code == 404


def test_transition_cannot_skip_the_confirm_queue(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    response = client.post(f"/interviews/{interview_id}/transition", json={"stage": "scheduled"})
    assert response.status_code == 409  # detected -> scheduled is no longer a thing


def test_transition_happy_path_after_confirm(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    client.post(f"/interviews/{interview_id}/confirm")
    response = client.post(f"/interviews/{interview_id}/transition", json={"stage": "scheduled"})
    assert response.status_code == 200
    assert response.json()["stage"] == "scheduled"


def test_transition_unknown_stage_422(
    client: TestClient, repo: InMemoryInterviewRepository
) -> None:
    interview_id = _seed(repo)
    response = client.post(f"/interviews/{interview_id}/transition", json={"stage": "ghosted"})
    assert response.status_code == 422


def test_transition_missing_404(client: TestClient) -> None:
    assert client.post("/interviews/999/transition", json={"stage": "confirmed"}).status_code == 404
