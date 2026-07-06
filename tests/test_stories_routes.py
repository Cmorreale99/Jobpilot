"""HTTP tests for the project-story review API (V3 Phase 3).

The runtime half of the resume-ready gate: synthesize the cards, then prove the API
approves a complete story, refuses an incomplete one with 409, closes gaps through typed
answers, and excludes with a required reason — the story-card review the V3 audit made
the primary workflow in place of a 148-item claim queue.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.main import create_app
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.validation_run_log import InMemoryValidationRunLog
from fastapi.testclient import TestClient

from tests.live_corpus import LIVE_USER, LiveCorpus, build_live_corpus


@pytest.fixture
def corpus() -> LiveCorpus:
    return build_live_corpus()


@pytest.fixture
def client(corpus: LiveCorpus) -> TestClient:
    app = create_app(
        settings=Settings(),
        claim_repository=corpus.repository,
        story_repository=InMemoryProjectStoryRepository(),
        validation_log=InMemoryValidationRunLog(),
    )
    return TestClient(app)


def _cards(client: TestClient) -> dict[str, dict]:
    report = client.post("/stories/synthesize", params={"user_id": LIVE_USER})
    assert report.status_code == 200
    listing = client.get("/stories", params={"user_id": LIVE_USER, "status": "pending_review"})
    assert listing.status_code == 200
    return {card["experience_name"]: card for card in listing.json()}


# --- synthesize + queue ------------------------------------------------------------------


def test_synthesize_then_queue_serves_cards_with_evidence(client: TestClient) -> None:
    report = client.post("/stories/synthesize", params={"user_id": LIVE_USER})
    assert report.status_code == 200
    assert len(report.json()["synthesized"]) == 15
    assert report.json()["quarantined"] == []

    cards = _cards(client)
    assert len(cards) == 15
    massdep = cards["MassDEP"]
    assert massdep["readiness"]["resume_ready"] is True
    assert massdep["problem"]["evidence"]  # a cited quote under the Problem
    assert massdep["actions"] and massdep["actions"][0]["evidence"]
    assert massdep["results"] and massdep["results"][0]["evidence"]


def test_get_single_card_and_404(client: TestClient) -> None:
    massdep = _cards(client)["MassDEP"]
    got = client.get(f"/stories/{massdep['id']}")
    assert got.status_code == 200 and got.json()["id"] == massdep["id"]
    assert client.get("/stories/99999").status_code == 404


# --- the runtime resume-ready gate -------------------------------------------------------


def test_approve_resume_ready_story(client: TestClient) -> None:
    massdep = _cards(client)["MassDEP"]
    response = client.post(f"/stories/{massdep['id']}/approve")
    assert response.status_code == 200
    assert response.json()["review_status"] == "approved"
    assert response.json()["reviewed_at"] is not None


def test_approve_incomplete_story_is_409(client: TestClient) -> None:
    cooper = _cards(client)["Cooper.ai"]  # a Problem and Actions, but no Result
    assert cooper["readiness"]["resume_ready"] is False
    response = client.post(f"/stories/{cooper['id']}/approve")
    assert response.status_code == 409
    assert "Result" in response.json()["detail"]


def test_approve_unknown_story_is_404(client: TestClient) -> None:
    assert client.post("/stories/99999/approve").status_code == 404


# --- answering questions (story-scoped attestations) -------------------------------------


def test_answer_missing_result_then_approve(client: TestClient) -> None:
    cooper = _cards(client)["Cooper.ai"]
    assert "missing_result" in {q["kind"] for q in cooper["questions"]}

    answered = client.post(
        f"/stories/{cooper['id']}/answer",
        json={"component": "result", "text": "Cut onboarding time from two weeks to three days"},
    )
    assert answered.status_code == 200
    card = answered.json()
    assert card["readiness"]["result"] == "user_attested"
    assert card["readiness"]["resume_ready"] is True
    assert card["results"][0]["text"] == "Cut onboarding time from two weeks to three days"

    approve = client.post(f"/stories/{cooper['id']}/approve")
    assert approve.status_code == 200
    assert approve.json()["review_status"] == "approved"


def test_weak_problem_answer_is_422(client: TestClient) -> None:
    pkos = _cards(client)["personal-knowledge-os"]  # the 28/2/0 case: no problem-space
    response = client.post(
        f"/stories/{pkos['id']}/answer", json={"component": "problem", "text": "faster"}
    )
    assert response.status_code == 422
    assert "specific" in response.json()["detail"]


def test_answer_with_invalid_component_is_422(client: TestClient) -> None:
    massdep = _cards(client)["MassDEP"]
    response = client.post(
        f"/stories/{massdep['id']}/answer",
        json={"component": "actions", "text": "Built the pipeline with Python and SQL"},
    )
    assert response.status_code == 422
    assert "component must be" in response.json()["detail"]


def test_answer_on_a_decided_story_is_409(client: TestClient) -> None:
    massdep = _cards(client)["MassDEP"]
    client.post(f"/stories/{massdep['id']}/approve")
    response = client.post(
        f"/stories/{massdep['id']}/answer",
        json={"component": "problem", "text": "Analysts reconciled permit exports by hand weekly"},
    )
    assert response.status_code == 409


# --- exclude ------------------------------------------------------------------------------


def test_exclude_requires_a_reason(client: TestClient) -> None:
    ds4635 = _cards(client)["DS4635"]  # claim-less coursework — an include-or-inventory card
    assert client.post(f"/stories/{ds4635['id']}/exclude", json={}).status_code == 422
    ok = client.post(
        f"/stories/{ds4635['id']}/exclude", json={"reason": "coursework, not resume material"}
    )
    assert ok.status_code == 200
    assert ok.json()["review_status"] == "excluded"
    assert ok.json()["decision_note"] == "coursework, not resume material"


# --- cross-source conflict surfaces as a question, not a silent pick ----------------------


def test_metric_conflict_surfaces_on_the_card(client: TestClient) -> None:
    massdep = _cards(client)["MassDEP"]  # carries the two-resume conflicting export metric
    conflict = [q for q in massdep["questions"] if q["kind"] == "metric_conflict"]
    assert len(conflict) == 1
    assert len(conflict[0]["quotes"]) == 2
