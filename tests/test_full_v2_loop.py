"""M12 exit test: the full V2 fixture loop, end to end.

extract → review (approve / attest / reject via the API) → render → download,
with every rendered bullet click-traceable to its evidence. Runs entirely on
fixtures with zero real credentials — the V2 Definition of Done in one test.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.config import Settings
from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    ClaimStatus,
    evidence_source_url,
)
from app.domain.jobs import canonical_job_url
from app.domain.resume_context import render_claim_bullet
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.main import create_app
from app.services.artifact_store import InMemoryArtifactStore
from app.services.claim_extraction import run_claim_extraction
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from fastapi.testclient import TestClient

from tests.conftest import confirm_source_roster

CLAIMS_FIXTURES = Path(__file__).parent / "fixtures" / "claims"
PROFILE = Path(__file__).parent / "fixtures" / "render" / "profile.json"
TEMPLATE = Path(__file__).parent.parent / "templates" / "resume_template.docx"


async def test_full_fixture_loop_extract_review_approve_render_download(
    tmp_path: Path,
) -> None:
    repo = InMemoryClaimRepository()
    settings = Settings(
        gdrive_mcp_enabled=False,
        gdrive_source_folder_id="career_docs",
        gdrive_mock_fixtures_dir=str(CLAIMS_FIXTURES / "drive"),
        github_mcp_enabled=False,
        github_username="jordanrivera",
        github_mock_fixtures_dir=str(CLAIMS_FIXTURES / "github"),
        resume_template_path=str(TEMPLATE),
        resume_profile_path=str(PROFILE),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    client = TestClient(
        create_app(
            settings=settings,
            claim_repository=repo,
            snapshot_store=InMemorySnapshotStore(),
            artifact_store=InMemoryArtifactStore(),
        )
    )

    # 1. ROSTER + EXTRACT — confirmed per-source roster (extraction refuses without
    # one), then mocked Drive + GitHub through the real policies and validator.
    await confirm_source_roster(
        MockDriveClient(CLAIMS_FIXTURES / "drive"),
        MockGitHubClient(CLAIMS_FIXTURES / "github"),
        "u1",
        repo,
        settings,
    )
    report = await run_claim_extraction("u1", repo, settings)
    assert report.claims  # extraction produced pending claims, none approved
    assert all(c.status is ClaimStatus.PENDING_REVIEW for c in report.claims)

    # 2. REVIEW — the queue serves review cards with evidence quotes + source links.
    queue = client.get("/claims/queue", params={"user_id": "u1"}).json()
    assert len(queue) == len(report.claims)
    for card in queue:
        assert card["evidence"], "every review card carries cited evidence"
        for evidence in card["evidence"]:
            assert evidence["chunk_text"]  # the quote is right there on the card
            # Click-through: external sources resolve to a URL.
            if evidence["source_type"] in ("github_commit", "github_readme", "drive"):
                assert evidence["source_url"], f"no click-through URL for {evidence['source_type']}"

    by_action = {card["action_text"]: card for card in queue}

    # Approve the clean claim as-is.
    clean = by_action["Automated exception triage with Python and SQL rules over the carrier feed."]
    assert clean["validation_flags"] == []
    assert client.post(f"/claims/{clean['id']}/approve").status_code == 200

    # Resolve a Missing-Results claim by attesting the impact (user_attested).
    missing_queue = client.get(
        "/claims/queue", params={"user_id": "u1", "missing_results": "true"}
    ).json()
    assert missing_queue
    resolved_card = missing_queue[0]
    attested = client.post(
        f"/claims/{resolved_card['id']}/edit-approve",
        json={
            "result_text": "Cut carrier onboarding time from two weeks to three days",
            "result_kind": "quantified",
        },
    )
    assert attested.status_code == 200
    assert attested.json()["result_status"] == "user_attested"

    # Reject the coupling-flagged claim with a reason (kept as labeled data).
    flagged = next(card for card in queue if card["validation_flags"])
    rejected = client.post(
        f"/claims/{flagged['id']}/reject", json={"reason": "result does not follow"}
    )
    assert rejected.status_code == 200

    # 3. RENDER — approved claims only, through the frozen renderer.
    rendered = client.post("/master-cv/render", params={"user_id": "u1"})
    assert rendered.status_code == 200
    version = rendered.json()["master_cv_version"]
    assert version == 1

    # 4. DOWNLOAD — a real docx comes back over the API.
    download = client.get("/master-cv/download", params={"user_id": "u1"})
    assert download.status_code == 200
    assert download.content[:2] == b"PK"

    # 5. TRACEABILITY — every rendered bullet maps to an approved claim whose evidence
    # is click-through (a source URL) or an explicit user attestation.
    context = json.loads(
        (tmp_path / "artifacts" / "u1" / f"master_cv_v{version}.json").read_text(encoding="utf-8")
    )
    rendered_bullets = [
        bullet
        for group in ("experiences", "projects_and_hackathons")
        for entry in context[group]
        for bullet in entry["bullets"]
    ]
    assert rendered_bullets

    approved = repo.list_claims("u1", status=ClaimStatus.APPROVED)
    bullets_by_text = {render_claim_bullet(c): c for c in approved}
    for bullet in rendered_bullets:
        claim = bullets_by_text[bullet]  # KeyError = a bullet with no claim behind it
        traceable = []
        for link in claim.evidence:
            evidence = repo.get_evidence(link.evidence_id)
            assert evidence is not None
            url = evidence_source_url(evidence.source_type, evidence.source_ref)
            traceable.append(url is not None or evidence.source_type == SOURCE_USER_ATTESTATION)
        assert any(traceable), f"bullet not click-traceable: {bullet}"

    # The rejected claim's text is nowhere in the rendered output.
    docx_files = list((tmp_path / "artifacts" / "u1").glob("*.docx"))
    xml = zipfile.ZipFile(docx_files[0]).read("word/document.xml").decode("utf-8")
    assert flagged["action_text"] not in xml


# --- link resolution units --------------------------------------------------------------


def test_canonical_job_url_strips_tracking_only() -> None:
    canonical = canonical_job_url(
        "https://Boards.example.com/jobs/123?gh_jid=42&utm_source=x&fbclid=y#apply"
    )
    assert canonical == "https://boards.example.com/jobs/123?gh_jid=42"
    assert canonical_job_url("not a url") is None
    assert canonical_job_url(None) is None
    assert canonical_job_url("/relative/path") is None


def test_evidence_source_urls_resolve_by_type() -> None:
    assert (
        evidence_source_url("github_commit", "jordanrivera/carrier-etl@c002outcome")
        == "https://github.com/jordanrivera/carrier-etl/commit/c002outcome"
    )
    assert (
        evidence_source_url("github_readme", "jordanrivera/carrier-etl")
        == "https://github.com/jordanrivera/carrier-etl"
    )
    assert evidence_source_url("drive", "drv_recon_001") == (
        "https://drive.google.com/open?id=drv_recon_001"
    )
    assert evidence_source_url("user_attestation", "claim:1:result") is None
    assert evidence_source_url("upload", "notes.md") is None
