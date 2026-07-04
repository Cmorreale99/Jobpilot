"""Dashboard read routes + application transitions over injected in-memory repositories."""

from __future__ import annotations

import pytest
from app.domain.claims import (
    SOURCE_DRIVE,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.domain.jobs import Job, JobMatch
from app.domain.master_cv_snapshot import StoredSnapshot
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials
from app.main import create_app
from app.services.application_repository import InMemoryApplicationRepository
from app.services.claim_repository import InMemoryClaimRepository
from app.services.job_repository import InMemoryJobRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore, create_master_cv_snapshot
from fastapi.testclient import TestClient

_JOB = Job(
    source="mock",
    external_id="job-1001",
    title="Staff Backend Engineer, Payments",
    company="Ledgerline",
    description="Settlement workers over Kafka.",
    url="https://example.com/jobs/1001",
    remote=True,
)


def _snapshot_approved_claim(store: InMemorySnapshotStore) -> StoredSnapshot:
    """Approve one claim and snapshot it — the only way a Master CV version is born."""
    repo = InMemoryClaimRepository()
    experience = repo.upsert_experience(
        "u1", ExperienceSeed(name="Ledgerline", section=ExperienceSection.PROFESSIONAL_EXPERIENCE)
    )
    chunk = EvidenceChunk(SOURCE_DRIVE, "drv1", "Re-architected the settlement pipeline.")
    (claim,) = repo.replace_unreviewed_claims(
        "u1",
        experience.id,
        [
            StorableClaim(
                draft=DraftClaim(
                    action_text="Re-architected the settlement pipeline",
                    action_tools=("Kafka",),
                    result_text="Cut runtime by 70%",
                    result_kind=ResultKind.QUALITATIVE_EVIDENCED,
                    evidence=(ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
            )
        ],
    )
    repo.transition_claim(claim.id, ClaimStatus.APPROVED)
    return create_master_cv_snapshot("u1", repo, store)


@pytest.fixture
def repos() -> tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore]:
    return (
        InMemoryApplicationRepository(),
        InMemoryJobRepository(),
        InMemorySnapshotStore(),
    )


@pytest.fixture
def client(
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> TestClient:
    applications, jobs, snapshots = repos
    return TestClient(
        create_app(
            application_repository=applications,
            job_repository=jobs,
            snapshot_store=snapshots,
        )
    )


def test_latest_master_cv_404_before_first_snapshot(client: TestClient) -> None:
    response = client.get("/master-cv/latest", params={"user_id": "u1"})
    assert response.status_code == 404


def test_latest_master_cv_serializes_approved_claims(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    _, _, snapshots = repos
    _snapshot_approved_claim(snapshots)
    body = client.get("/master-cv/latest", params={"user_id": "u1"}).json()
    assert body["version"] == 1
    assert body["claim_count"] == 1
    assert body["claims"][0]["action"] == "Re-architected the settlement pipeline"
    assert body["claims"][0]["result"] == "Cut runtime by 70%"
    assert body["claims"][0]["source_type"] == "approved_claim"
    assert [e["name"] for e in body["sections"]["professional_experience"]] == ["Ledgerline"]


def test_matches_empty_before_master_cv_exists(client: TestClient) -> None:
    body = client.get("/matches", params={"user_id": "u1"}).json()
    assert body == {"master_cv_version": None, "matches": []}


def test_matches_returns_ranked_matches_for_latest_version(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    _, jobs, snapshots = repos
    stored = _snapshot_approved_claim(snapshots)
    jobs.upsert_jobs([_JOB])
    jobs.save_matches(
        "u1",
        stored.version,
        [JobMatch(job=_JOB, score=0.9, rank=1, rationale="strong fit", matched_terms=("kafka",))],
    )
    body = client.get("/matches", params={"user_id": "u1"}).json()
    assert body["master_cv_version"] == stored.version
    (match,) = body["matches"]
    assert match["rank"] == 1
    assert match["rationale"] == "strong fit"
    assert match["job"]["company"] == "Ledgerline"
    assert match["job"]["url"] == "https://example.com/jobs/1001"


def _seed_application(
    applications: InMemoryApplicationRepository, *, with_outreach: bool = True
) -> int:
    materials = TailoredMaterials(summary="fit", highlights=("h1",), cover_letter="letter")
    application = applications.get_or_create_application("u1", _JOB, 1, materials)
    if with_outreach:
        applications.upsert_draft(
            application.id,
            OutreachMessage(subject="Regarding the role", body="Hi Priya,"),
            Contact(name="Priya Raman", source="fixture"),
        )
    return application.id


def test_applications_list_includes_allowed_transitions(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    applications, _, _ = repos
    _seed_application(applications)
    (entry,) = client.get("/applications", params={"user_id": "u1"}).json()
    assert entry["status"] == "drafted"
    assert entry["allowed_transitions"] == ["applied", "ignored"]
    assert entry["materials"]["highlights"] == ["h1"]


def test_application_detail_includes_outreach_draft(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications)
    body = client.get(f"/applications/{application_id}").json()
    assert body["job_company"] == "Ledgerline"
    assert body["outreach"]["subject"] == "Regarding the role"
    assert body["outreach"]["contact"]["name"] == "Priya Raman"


def test_application_detail_without_outreach(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications, with_outreach=False)
    assert client.get(f"/applications/{application_id}").json()["outreach"] is None


def test_application_detail_404(client: TestClient) -> None:
    assert client.get("/applications/999").status_code == 404


def test_transition_application_happy_path(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications)
    response = client.post(f"/applications/{application_id}/transition", json={"status": "applied"})
    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert response.json()["allowed_transitions"] == ["interviewing", "offer", "rejected"]


def test_transition_rejects_illegal_jump_with_409(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications)
    response = client.post(f"/applications/{application_id}/transition", json={"status": "offer"})
    assert response.status_code == 409


def test_transition_unknown_status_is_422(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemorySnapshotStore],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications)
    response = client.post(f"/applications/{application_id}/transition", json={"status": "yolo"})
    assert response.status_code == 422


def test_transition_missing_application_is_404(client: TestClient) -> None:
    response = client.post("/applications/999/transition", json={"status": "applied"})
    assert response.status_code == 404


def test_cors_allows_dashboard_origin(client: TestClient) -> None:
    response = client.get(
        "/applications",
        params={"user_id": "u1"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
