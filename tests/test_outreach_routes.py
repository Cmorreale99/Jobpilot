"""Approval-queue HTTP routes over an injected in-memory repository."""

from __future__ import annotations

import pytest
from app.domain.jobs import Job
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials
from app.main import create_app
from app.services.application_repository import InMemoryApplicationRepository
from fastapi.testclient import TestClient


@pytest.fixture
def repo() -> InMemoryApplicationRepository:
    return InMemoryApplicationRepository()


@pytest.fixture
def client(repo: InMemoryApplicationRepository) -> TestClient:
    return TestClient(create_app(application_repository=repo))


def _seed_draft(repo: InMemoryApplicationRepository, external_id: str = "j1") -> int:
    job = Job(
        source="mock",
        external_id=external_id,
        title="Staff Engineer",
        company="Ledgerline",
        description="x",
    )
    materials = TailoredMaterials(summary="fit", highlights=("h1",), cover_letter="letter")
    application = repo.get_or_create_application("u1", job, 1, materials)
    record = repo.upsert_draft(
        application.id,
        OutreachMessage(subject="Regarding the role", body="Hi Priya,"),
        Contact(name="Priya Raman", source="fixture"),
    )
    return record.id


def test_queue_lists_pending_drafts_with_job_context(
    client: TestClient, repo: InMemoryApplicationRepository
) -> None:
    outreach_id = _seed_draft(repo)
    response = client.get("/outreach/queue", params={"user_id": "u1"})
    assert response.status_code == 200
    (entry,) = response.json()
    assert entry["id"] == outreach_id
    assert entry["status"] == "drafted"
    assert entry["subject"] == "Regarding the role"
    assert entry["contact"]["name"] == "Priya Raman"
    assert entry["job_title"] == "Staff Engineer"
    assert entry["job_company"] == "Ledgerline"


def test_queue_is_empty_for_other_users(
    client: TestClient, repo: InMemoryApplicationRepository
) -> None:
    _seed_draft(repo)
    response = client.get("/outreach/queue", params={"user_id": "someone-else"})
    assert response.status_code == 200
    assert response.json() == []


def test_approve_moves_draft_out_of_queue(
    client: TestClient, repo: InMemoryApplicationRepository
) -> None:
    outreach_id = _seed_draft(repo)
    response = client.post(f"/outreach/{outreach_id}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert client.get("/outreach/queue", params={"user_id": "u1"}).json() == []


def test_discard_is_terminal(client: TestClient, repo: InMemoryApplicationRepository) -> None:
    outreach_id = _seed_draft(repo)
    assert client.post(f"/outreach/{outreach_id}/discard").status_code == 200
    # A discarded draft cannot be approved afterwards.
    assert client.post(f"/outreach/{outreach_id}/approve").status_code == 409


def test_double_approve_conflicts(client: TestClient, repo: InMemoryApplicationRepository) -> None:
    outreach_id = _seed_draft(repo)
    assert client.post(f"/outreach/{outreach_id}/approve").status_code == 200
    assert client.post(f"/outreach/{outreach_id}/approve").status_code == 409


def test_missing_draft_is_404(client: TestClient) -> None:
    assert client.post("/outreach/999/approve").status_code == 404
