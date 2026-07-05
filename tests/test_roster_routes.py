"""Roster HTTP routes over the injected in-memory repository."""

from __future__ import annotations

import pytest
from app.domain.claims import (
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
)
from app.main import create_app
from app.services.claim_repository import InMemoryClaimRepository
from fastapi.testclient import TestClient


@pytest.fixture
def repo() -> InMemoryClaimRepository:
    return InMemoryClaimRepository()


@pytest.fixture
def client(repo: InMemoryClaimRepository) -> TestClient:
    return TestClient(create_app(claim_repository=repo))


def _propose(repo: InMemoryClaimRepository, name: str) -> int:
    seed = ExperienceSeed(
        name=name,
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        kind=ExperienceKind.EMPLOYER_ROLE,
    )
    return repo.propose_experience("u1", seed).id


def test_roster_lists_entities_with_lifecycle_fields(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    _propose(repo, "resume_final (1).pdf")
    (entry,) = client.get("/roster", params={"user_id": "u1"}).json()
    assert entry["status"] == "proposed"
    assert entry["kind"] == "employer_role"
    assert entry["assigned_chunks"] == 0


def test_confirm_then_reconfirm_conflicts(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    entity_id = _propose(repo, "Wellington")
    ok = client.post(f"/roster/{entity_id}/confirm")
    assert ok.status_code == 200 and ok.json()["status"] == "confirmed"
    assert client.post(f"/roster/{entity_id}/confirm").status_code == 409


def test_rename_keeps_alias_and_discard_is_terminal(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    entity_id = _propose(repo, "resume_final (1).pdf")
    renamed = client.patch(
        f"/roster/{entity_id}",
        json={"name": "Coopersmith Data — Data Engineer", "dates": "Jun 2025–Present"},
    ).json()
    assert renamed["name"] == "Coopersmith Data — Data Engineer"
    assert "resume_final (1).pdf" in renamed["aliases"]

    assert client.post(f"/roster/{entity_id}/discard").status_code == 200
    assert client.post(f"/roster/{entity_id}/confirm").status_code == 409


def test_merge_moves_into_confirmed_target(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    target_id = _propose(repo, "Coopersmith Data — Data Engineer")
    repo.set_experience_status(target_id, ExperienceStatus.CONFIRMED)
    source_id = _propose(repo, "resume_final_FINAL.pdf")

    merged = client.post(
        "/roster/merge", json={"source_id": source_id, "target_id": target_id}
    ).json()
    assert "resume_final_FINAL.pdf" in merged["aliases"]
    roster = client.get("/roster", params={"user_id": "u1"}).json()
    source_row = next(r for r in roster if r["id"] == source_id)
    assert source_row["status"] == "merged"
    assert source_row["merged_into_id"] == target_id


def test_merge_into_unconfirmed_target_is_409(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    a, b = _propose(repo, "A"), _propose(repo, "B")
    response = client.post("/roster/merge", json={"source_id": a, "target_id": b})
    assert response.status_code == 409


def test_unknown_entity_is_404(client: TestClient) -> None:
    assert client.post("/roster/999/confirm").status_code == 404
    assert client.patch("/roster/999", json={"name": "x"}).status_code == 404
