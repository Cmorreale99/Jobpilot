"""End-to-end known-truth cases (MASTER CV REPAIR §11, §16.2/16.8/16.12/16.13/16.16).

The portfolio universe, source → Master CV: `Cameron-Morreale-portfolio` is a source
container (never a career project); Paper Recommender and OneWorld are real child
projects whose evidence — root-README sections AND nested READMEs — lands under THEM;
repo-wide portfolio commits stay unresolved; the OneWorld metric renders at most once
and never as a portfolio accomplishment; a required-source failure blocks publication
until a later gather succeeds; and every disposition is visible, not a log line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.domain.claims import SOURCE_GITHUB_DOC, ExperienceStatus
from app.domain.project_story import StoryReviewStatus
from app.domain.validation_runs import KIND_MASTER_CV_PUBLICATION
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_extraction import run_claim_extraction
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.roster import run_roster_assignment, run_roster_detection
from app.services.source_capture import InMemorySourceCaptureStore
from app.services.story_review import approve_story, attest_story_component
from app.services.story_snapshot import SourceCompletenessError, create_story_snapshot
from app.services.story_synthesis import run_story_synthesis
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.test_collection_assignment import _settings as universe_settings
from tests.test_github_universe import EmptyDriveClient

FIXTURES = Path(__file__).parent / "fixtures" / "github_universe"
USER = "u1"
PORTFOLIO_REPO = "cmorreale/Cameron-Morreale-portfolio"
METRIC = "Top 3 out of 100+ teams"


@pytest.fixture
async def pipeline_state():
    """Detect → human roster decisions → assign → extract → synthesize (all offline)."""
    settings = universe_settings()
    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    capture = InMemorySourceCaptureStore()
    log = InMemoryValidationRunLog()
    github = MockGitHubClient(FIXTURES)

    await run_roster_detection(
        EmptyDriveClient(), github, USER, claims_repo, settings, capture_store=capture
    )
    proposals = {e.name: e for e in claims_repo.list_experiences(USER)}
    # The user's roster review (§7.3): children + the single-project repo confirmed;
    # the portfolio CONTAINER and junk repos discarded.
    for name, experience in proposals.items():
        if name in ("jobpilot", "paper-recommender", "oneworld"):
            claims_repo.set_experience_status(experience.id, ExperienceStatus.CONFIRMED)
        else:
            claims_repo.set_experience_status(experience.id, ExperienceStatus.DISCARDED)

    assignment = await run_roster_assignment(
        EmptyDriveClient(),
        github,
        USER,
        claims_repo,
        settings,
        capture_store=capture,
        validation_log=log,
    )
    await run_claim_extraction(USER, claims_repo, settings)
    run_story_synthesis(USER, claims_repo, stories)
    decided = {e.name: e for e in claims_repo.list_experiences(USER)}
    return claims_repo, stories, capture, log, assignment, decided


@pytest.mark.asyncio
async def test_portfolio_container_is_never_a_project(pipeline_state) -> None:  # type: ignore[no-untyped-def]
    claims_repo, stories, _, _, _, proposals = pipeline_state
    # 11.1: the container was PROPOSED (visible, flagged) — never canonical.
    portfolio = proposals["Cameron-Morreale-portfolio"]
    assert portfolio.status is ExperienceStatus.DISCARDED
    # No story exists for it, and no evidence is canonically owned by it.
    assert stories.list_stories_for_experience(USER, portfolio.id) == []
    owned = [
        row for row in claims_repo.list_all_evidence(USER) if row.experience_id == portfolio.id
    ]
    assert owned == [], "the discarded container must own no evidence"


@pytest.mark.asyncio
async def test_paper_recommender_end_to_end(pipeline_state) -> None:  # type: ignore[no-untyped-def]
    """§16.13: discovered, captured, structured, identified, assigned, retained,
    inventory-visible, not absorbed, disposition visible — not a substring check."""
    claims_repo, stories, capture, _, _, proposals = pipeline_state
    paper = proposals["paper-recommender"]
    nested_ref = f"{PORTFOLIO_REPO}/projects/paper-recommender/README.md"

    # Captured: the nested README is a raw-captured document with an active version.
    version = capture.get_active_version(USER, SOURCE_GITHUB_DOC, nested_ref)
    assert version is not None, "nested README was not raw-captured"
    # Structured: its element tree exists.
    assert capture.list_elements(version.id), "nested README has no element tree"
    # Identified + assigned + retained: evidence rows belong to the PR entity.
    rows = [
        row
        for row in claims_repo.list_all_evidence(USER)
        if row.experience_id == paper.id and row.is_active
    ]
    assert rows, "Paper Recommender owns no evidence"
    assert any("Restored date coverage" in row.chunk_text for row in rows), (
        "the PR evidence content did not survive"
    )
    # Not absorbed: the root README's PR section is PR-owned too (not the container's).
    assert any(row.source_ref.startswith(PORTFOLIO_REPO) for row in rows), (
        "the portfolio README's PR section must contribute to the child project"
    )
    # Disposition visible: a story card exists for the entity (readiness questions and
    # all) — the Master CV disposition is a reviewable object, not a log line.
    story = stories.get_story_for_experience(USER, paper.id)
    assert story is not None
    assert story.review_status in (StoryReviewStatus.PENDING_REVIEW, StoryReviewStatus.DRAFT)


@pytest.mark.asyncio
async def test_oneworld_metric_owned_once_and_publication_gates(pipeline_state) -> None:  # type: ignore[no-untyped-def]
    """§11.4 + §16.12 + §16.16: one canonical owner for the shared metric; the failed
    required source blocks publication; the published CV excludes the container."""
    claims_repo, stories, _, log, _, proposals = pipeline_state
    oneworld = proposals["oneworld"]

    # The duplicated metric (portfolio README section + nested OneWorld README) is
    # owned by ONE entity — cross-entity double-counting is structurally impossible.
    metric_rows = [
        row
        for row in claims_repo.list_all_evidence(USER)
        if METRIC in row.chunk_text and row.is_active
    ]
    assert metric_rows, "the OneWorld metric evidence is missing"
    owners = {row.experience_id for row in metric_rows}
    assert owners == {oneworld.id}, (
        f"the shared metric must belong to OneWorld alone, got owners {owners}"
    )

    # Make the OneWorld story resume-ready through the honest path: attestation.
    story = stories.get_story_for_experience(USER, oneworld.id)
    assert story is not None
    attest_story_component(
        stories,
        claims_repo,
        story.id,
        "problem",
        "Underserved communities lacked reachable triage, costing clinics time on "
        "avoidable in-person visits",
    )
    attest_story_component(
        stories, claims_repo, story.id, "result", f"Placed {METRIC} at the hackathon"
    )
    approve_story(stories, claims_repo, story.id)

    # §16.16/§14.1: the gather recorded a required-source failure (broken-readme) —
    # publication is blocked and nothing is written.
    store = InMemorySnapshotStore()
    with pytest.raises(SourceCompletenessError, match="broken-readme"):
        create_story_snapshot(USER, stories, claims_repo, store, validation_log=log)
    assert store.get_latest(USER) is None, "a blocked candidate must write nothing"

    # The user repairs the source; a newer successful gather supersedes the failure.
    log.record(USER, "source_gather", subject_ref="sources", passed=True)
    snapshot = create_story_snapshot(USER, stories, claims_repo, store, validation_log=log)

    # The container renders as PROVENANCE only (§11.1: repository identity is
    # provenance, not project identity): no rendered STORY/entity is the portfolio.
    entries = [entry for section in snapshot.content["sections"].values() for entry in section]
    assert entries, "the snapshot rendered no stories"
    assert all("portfolio" not in entry["name"].lower() for entry in entries), (
        "the rendered Master CV must not contain a portfolio project"
    )
    bullet_texts = " ".join(bullet["text"] for entry in entries for bullet in entry["bullets"])
    assert bullet_texts.count(METRIC) <= 1, "the shared metric must not double-count"
    # The publication dispositions are recorded and queryable.
    runs = log.list_runs(USER, KIND_MASTER_CV_PUBLICATION)
    assert runs and any("rendered" in line for line in runs[-1].detail)


@pytest.mark.asyncio
async def test_late_document_content_is_still_discovered() -> None:
    """§16.8: a project appearing deep in a long document is still discovered and its
    section still receives an ownership decision — length is never silent truncation."""
    from app.domain.claims import ExperienceKind, ExperienceSection, ExperienceSeed
    from app.domain.project_reconciliation import STATUS_DETECTED
    from app.services.roster import run_project_reconciliation

    filler = "\n\n".join(
        f"## Routine appendix {i}\n\nUnremarkable filler prose, section {i}." for i in range(400)
    )
    late_project = (
        "## Glacier Telemetry\n\nBuilt the Glacier telemetry ingest system in Rust, "
        "restoring sensor coverage across 40 stations."
    )
    long_doc = f"# Field Notes\n\n{filler}\n\n{late_project}\n"

    class LongDocDrive:
        async def list_candidate_sources(self, user_id):
            from app.integrations.base import DriveSource

            return [DriveSource(source_ref="long_doc", title="Field Notes", mime_type="text/plain")]

        async def read_source(self, source_ref):
            from app.integrations.base import DriveDocument

            return DriveDocument(
                source_ref="long_doc", title="Field Notes", mime_type="text/plain", text=long_doc
            )

        async def get_source_metadata(self, source_ref):  # pragma: no cover
            raise AssertionError

        async def list_changed_sources(self, user_id, since):  # pragma: no cover
            return []

    class NoRepos:
        async def list_candidate_repos(self, user_id):
            return []

        async def list_changed_repos(self, user_id, since):  # pragma: no cover
            return []

    from app.config import Settings

    settings = Settings(gdrive_allow_broad_scan=True, github_username="", uploads_dir="")
    claims_repo = InMemoryClaimRepository()
    glacier = claims_repo.upsert_experience(
        USER,
        ExperienceSeed(
            name="Glacier Telemetry",
            section=ExperienceSection.PROJECTS_HACKATHONS,
            kind=ExperienceKind.PROJECT,
        ),
    )

    # Discovery: reconciliation over the raw text finds the late project.
    report = await run_project_reconciliation(
        LongDocDrive(), NoRepos(), USER, claims_repo, ["Glacier Telemetry"], settings
    )
    assert report.results[0].status == STATUS_DETECTED

    # Assignment: the deep section still receives its ownership decision.
    await run_roster_assignment(LongDocDrive(), NoRepos(), USER, claims_repo, settings)
    rows = [
        row
        for row in claims_repo.list_all_evidence(USER)
        if "Glacier telemetry ingest" in row.chunk_text
    ]
    assert rows, "the late section's evidence is missing"
    assert {row.experience_id for row in rows} == {glacier.id}
