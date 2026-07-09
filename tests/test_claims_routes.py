"""HTTP tests for the Master CV routes: the section picker, snapshot reads, render + download.

The V2 claim review queue (approve / edit-approve / reject) and ``POST /master-cv/snapshots``
were retired with the story layer; claims here are approved directly through the repository
to exercise the kept render path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
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


# --- snapshots (read) -----------------------------------------------------------------------


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
    repo.transition_claim(claim_id, ClaimStatus.APPROVED)

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
    repo.transition_claim(claim_id, ClaimStatus.APPROVED)
    response = client.post("/master-cv/render", params={"user_id": "u1"})
    assert response.status_code == 503  # unconfigured, never invented header data
