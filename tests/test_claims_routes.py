"""HTTP tests for the claim review API: queue, decisions, section picker, snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_USER_ATTESTATION,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    CostDimension,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    Inefficiency,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.main import create_app
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from fastapi.testclient import TestClient

_CHUNK = EvidenceChunk(
    SOURCE_GITHUB_COMMIT, "c001", "Fixed dedup key mismatch across four carriers with Kafka"
)


@pytest.fixture
def repo() -> InMemoryClaimRepository:
    return InMemoryClaimRepository()


@pytest.fixture
def client(repo: InMemoryClaimRepository) -> TestClient:
    from app.services.artifact_store import InMemoryArtifactStore

    app = create_app(
        settings=Settings(),
        claim_repository=repo,
        snapshot_store=InMemorySnapshotStore(),
        artifact_store=InMemoryArtifactStore(),
    )
    return TestClient(app)


def _seed_claim(
    repo: InMemoryClaimRepository,
    *,
    result_kind: ResultKind = ResultKind.MISSING,
    flags: tuple[str, ...] = (),
) -> int:
    experience = repo.upsert_experience(
        "u1", ExperienceSeed(name="carrier-etl", section=ExperienceSection.PROJECTS_HACKATHONS)
    )
    inserted = repo.replace_unreviewed_claims(
        "u1",
        experience.id,
        [
            StorableClaim(
                draft=DraftClaim(
                    action_text="Fixed dedup key mismatch across four carriers with Kafka",
                    action_tools=("Kafka",),
                    problem_text="Manual reconciliation took 10 hours per week",
                    problem_cost_dimension=CostDimension.TIME,
                    problem_inefficiency=Inefficiency.MANUAL,
                    result_kind=result_kind,
                    evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
                validation_flags=flags,
            )
        ],
    )
    return inserted[0].id


# --- queue -----------------------------------------------------------------------------


def test_queue_serves_review_cards_with_evidence_quotes(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    claim_id = _seed_claim(repo)
    response = client.get("/claims/queue", params={"user_id": "u1"})
    assert response.status_code == 200
    (card,) = response.json()
    assert card["id"] == claim_id
    assert card["status"] == "pending_review"
    (evidence,) = card["evidence"]
    assert evidence["source_type"] == SOURCE_GITHUB_COMMIT
    assert evidence["source_ref"] == "c001"
    assert "Fixed dedup key mismatch" in evidence["chunk_text"]


def test_queue_missing_results_filter(client: TestClient, repo: InMemoryClaimRepository) -> None:
    _seed_claim(repo, result_kind=ResultKind.MISSING)
    response = client.get("/claims/queue", params={"user_id": "u1", "missing_results": "true"})
    assert response.status_code == 200
    assert all(card["result_kind"] == "missing" for card in response.json())


# --- decisions ---------------------------------------------------------------------------


def test_approve_then_reapprove_conflicts(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    claim_id = _seed_claim(repo)
    ok = client.post(f"/claims/{claim_id}/approve")
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"
    again = client.post(f"/claims/{claim_id}/approve")
    assert again.status_code == 409


def test_approve_unknown_claim_is_404(client: TestClient) -> None:
    assert client.post("/claims/999/approve").status_code == 404


def test_approve_flagged_claim_is_409(client: TestClient, repo: InMemoryClaimRepository) -> None:
    claim_id = _seed_claim(repo, flags=("result_problem_coupling: …",))
    response = client.post(f"/claims/{claim_id}/approve")
    assert response.status_code == 409
    assert "cannot be approved as-is" in response.json()["detail"]
    # Edit-attest still resolves it: the human supersedes the validator, with provenance.
    resolved = client.post(
        f"/claims/{claim_id}/edit-approve",
        json={"result_text": "Cut effort from 10h to 1h", "result_kind": "quantified"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["validation_flags"] == []


def test_reject_requires_a_reason(client: TestClient, repo: InMemoryClaimRepository) -> None:
    claim_id = _seed_claim(repo)
    missing_body = client.post(f"/claims/{claim_id}/reject", json={})
    assert missing_body.status_code == 422
    whitespace = client.post(f"/claims/{claim_id}/reject", json={"reason": " "})
    assert whitespace.status_code == 422
    ok = client.post(f"/claims/{claim_id}/reject", json={"reason": "not my work"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "rejected"
    assert ok.json()["review_note"] == "not my work"


def test_edit_approve_attests_and_approves(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    claim_id = _seed_claim(repo)
    response = client.post(
        f"/claims/{claim_id}/edit-approve",
        json={
            "result_text": "Cut weekly reconciliation effort from 10 hours to 1 hour",
            "result_kind": "quantified",
            "result_metric_json": {"resolves": "time"},
        },
    )
    assert response.status_code == 200
    card = response.json()
    assert card["status"] == "approved"
    assert card["result_status"] == "user_attested"
    attestations = [e for e in card["evidence"] if e["source_type"] == SOURCE_USER_ATTESTATION]
    assert len(attestations) == 1
    assert attestations[0]["field"] == "result"
    assert attestations[0]["outcome_quote"] == (
        "Cut weekly reconciliation effort from 10 hours to 1 hour"
    )


def test_edit_approve_rejects_inconsistent_edits(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    claim_id = _seed_claim(repo)
    response = client.post(
        f"/claims/{claim_id}/edit-approve", json={"result_text": "Cut effort by 90%"}
    )
    assert response.status_code == 422  # result_text without an explicit result_kind


# --- section picker -----------------------------------------------------------------------


def test_layout_endpoint_reassigns_section_and_order(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    _seed_claim(repo)
    (experience,) = client.get("/experiences", params={"user_id": "u1"}).json()
    assert experience["section"] == "projects_hackathons"

    response = client.post(
        f"/experiences/{experience['id']}/layout",
        json={"section": "professional_experience", "sort_order": 2},
    )
    assert response.status_code == 200
    assert response.json()["section"] == "professional_experience"
    assert response.json()["sort_order"] == 2


def test_layout_requires_at_least_one_field(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    _seed_claim(repo)
    (experience,) = client.get("/experiences", params={"user_id": "u1"}).json()
    assert client.post(f"/experiences/{experience['id']}/layout", json={}).status_code == 422


def test_layout_unknown_experience_is_404(client: TestClient) -> None:
    response = client.post("/experiences/999/layout", json={"sort_order": 1})
    assert response.status_code == 404


# --- snapshots ------------------------------------------------------------------------------


def test_snapshot_endpoint_builds_from_approved_claims_only(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    claim_id = _seed_claim(repo)

    empty = client.post("/master-cv/snapshots", params={"user_id": "u1"})
    assert empty.status_code == 200
    assert empty.json()["content"]["sections"]["projects_hackathons"] == []

    client.post(f"/claims/{claim_id}/approve")
    snapshot = client.post("/master-cv/snapshots", params={"user_id": "u1"})
    entries = snapshot.json()["content"]["sections"]["projects_hackathons"]
    assert [c["claim_id"] for e in entries for c in e["claims"]] == [claim_id]

    latest = client.get("/master-cv/snapshots/latest", params={"user_id": "u1"})
    assert latest.status_code == 200
    assert latest.json()["version"] == snapshot.json()["version"]


def test_latest_snapshot_for_unknown_user_is_404(client: TestClient) -> None:
    assert client.get("/master-cv/snapshots/latest", params={"user_id": "ghost"}).status_code == 404


# --- render + download (M11) -----------------------------------------------------------


def test_render_and_download_master_cv(repo: InMemoryClaimRepository, tmp_path: Path) -> None:
    from app.services.artifact_store import InMemoryArtifactStore

    template = Path(__file__).parent.parent / "templates" / "resume_template.docx"
    profile = Path(__file__).parent / "fixtures" / "render" / "profile.json"
    app = create_app(
        settings=Settings(
            resume_template_path=str(template),
            resume_profile_path=str(profile),
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
        claim_repository=repo,
        snapshot_store=InMemorySnapshotStore(),
        artifact_store=InMemoryArtifactStore(),
    )
    render_client = TestClient(app)

    claim_id = _seed_claim(repo)
    render_client.post(f"/claims/{claim_id}/approve")

    rendered = render_client.post("/master-cv/render", params={"user_id": "u1"})
    assert rendered.status_code == 200
    assert rendered.json()["kind"] == "master_cv_docx"
    assert rendered.json()["master_cv_version"] == 1

    download = render_client.get("/master-cv/download", params={"user_id": "u1"})
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert download.content[:2] == b"PK"  # a real docx (zip) came back


def test_download_without_render_is_404(client: TestClient) -> None:
    assert client.get("/master-cv/download", params={"user_id": "u1"}).status_code == 404


def test_render_without_profile_is_503(client: TestClient, repo: InMemoryClaimRepository) -> None:
    claim_id = _seed_claim(repo)
    client.post(f"/claims/{claim_id}/approve")
    response = client.post("/master-cv/render", params={"user_id": "u1"})
    assert response.status_code == 503  # unconfigured, never invented header data
