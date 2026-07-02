"""Dashboard read routes + application transitions over injected in-memory repositories."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.cv import CvSource, MasterCv, ParClaim
from app.domain.jobs import Job, JobMatch
from app.domain.outreach import Contact, OutreachMessage
from app.domain.tailoring import TailoredMaterials
from app.main import create_app
from app.services.application_repository import InMemoryApplicationRepository
from app.services.job_repository import InMemoryJobRepository
from app.services.master_cv_repository import InMemoryMasterCvRepository
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


def _cv() -> MasterCv:
    return MasterCv(
        claims=[
            ParClaim(
                action="Re-architected the settlement pipeline",
                source_ref="resume",
                result="Cut runtime by 70%",
            )
        ],
        sources=[
            CvSource(
                source_type="gdrive",
                external_ref="resume",
                title="Resume",
                mime_type="text/plain",
                raw_text="…",
                ingested_at=datetime(2026, 7, 1, tzinfo=UTC),
            )
        ],
    )


@pytest.fixture
def repos() -> tuple[
    InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository
]:
    return (
        InMemoryApplicationRepository(),
        InMemoryJobRepository(),
        InMemoryMasterCvRepository(),
    )


@pytest.fixture
def client(
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> TestClient:
    applications, jobs, master_cvs = repos
    return TestClient(
        create_app(
            application_repository=applications,
            job_repository=jobs,
            master_cv_repository=master_cvs,
        )
    )


def test_latest_master_cv_404_before_first_ingest(client: TestClient) -> None:
    response = client.get("/master-cv/latest", params={"user_id": "u1"})
    assert response.status_code == 404


def test_latest_master_cv_serializes_claims_and_sources(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> None:
    _, _, master_cvs = repos
    master_cvs.save("u1", _cv())
    body = client.get("/master-cv/latest", params={"user_id": "u1"}).json()
    assert body["version"] == 1
    assert body["claim_count"] == 1
    assert body["claims"][0]["action"] == "Re-architected the settlement pipeline"
    assert body["sources"][0]["title"] == "Resume"


def test_matches_empty_before_master_cv_exists(client: TestClient) -> None:
    body = client.get("/matches", params={"user_id": "u1"}).json()
    assert body == {"master_cv_version": None, "matches": []}


def test_matches_returns_ranked_matches_for_latest_version(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> None:
    _, jobs, master_cvs = repos
    stored = master_cvs.save("u1", _cv())
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
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> None:
    applications, _, _ = repos
    _seed_application(applications)
    (entry,) = client.get("/applications", params={"user_id": "u1"}).json()
    assert entry["status"] == "drafted"
    assert entry["allowed_transitions"] == ["applied", "ignored"]
    assert entry["materials"]["highlights"] == ["h1"]


def test_application_detail_includes_outreach_draft(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications)
    body = client.get(f"/applications/{application_id}").json()
    assert body["job_company"] == "Ledgerline"
    assert body["outreach"]["subject"] == "Regarding the role"
    assert body["outreach"]["contact"]["name"] == "Priya Raman"


def test_application_detail_without_outreach(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications, with_outreach=False)
    assert client.get(f"/applications/{application_id}").json()["outreach"] is None


def test_application_detail_404(client: TestClient) -> None:
    assert client.get("/applications/999").status_code == 404


def test_transition_application_happy_path(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications)
    response = client.post(f"/applications/{application_id}/transition", json={"status": "applied"})
    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert response.json()["allowed_transitions"] == ["interviewing", "offer", "rejected"]


def test_transition_rejects_illegal_jump_with_409(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
) -> None:
    applications, _, _ = repos
    application_id = _seed_application(applications)
    response = client.post(f"/applications/{application_id}/transition", json={"status": "offer"})
    assert response.status_code == 409


def test_transition_unknown_status_is_422(
    client: TestClient,
    repos: tuple[InMemoryApplicationRepository, InMemoryJobRepository, InMemoryMasterCvRepository],
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
