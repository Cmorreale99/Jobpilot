"""Shared test fixtures for the Drive / Master CV slice.

All fixtures run fully offline: no env vars, no credentials, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.domain.chunking import chunk_normalized_text
from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    ClaimEvidenceRef,
    ClaimField,
    ClaimRepository,
    ClaimStatus,
    CostDimension,
    DraftClaim,
    EvidenceChunk,
    Experience,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    Inefficiency,
    ResultKind,
    ResultStatus,
    StorableClaim,
    span_ref,
)
from app.domain.project_story import StoryReviewStatus, select_story_content
from app.domain.roster import HeuristicRosterProposer
from app.integrations.base import DriveClient, GitHubClient
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.integrations.mock.jobs import MockJobSource
from app.integrations.mock.research import MockResearchClient
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.roster import gather_source_documents

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "drive"
GITHUB_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "github"
JOBS_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "jobs"
RESEARCH_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "research"
INBOX_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "inbox"
APPROVED_FOLDER_ID = "career_docs"
GITHUB_USERNAME = "jordanrivera"


@pytest.fixture(autouse=True)
def _ignore_developer_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: never read the developer's real ``.env``.

    Every ``Settings(...)`` constructed in tests sees only explicit kwargs and real
    env vars — a filled-in local ``.env`` (real tokens, enabled integrations) must
    not change test behavior.
    """
    monkeypatch.setattr(
        Settings, "model_config", {**Settings.model_config, "env_file": None}, raising=True
    )


async def confirm_source_roster(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    repository: ClaimRepository,
    settings: Settings,
) -> dict[str, Experience]:
    """Stand in for the human roster review at per-source granularity.

    Extraction refuses to run without a confirmed roster with assigned evidence (V3
    Phase 0 deleted the per-file fallback), so fixture tests bootstrap one explicitly:
    propose one entity per source document (the heuristic proposer), CONFIRM every
    proposal — the test playing the human — and assign each source's chunks to its
    own entity deterministically (no assigner heuristics, so fixture docs whose text
    never mentions their own title still get their evidence).

    Returns the confirmed entities keyed by casefolded name. Twin logic lives in
    ``app/tools/seed_v2_demo.py`` (the demo seeder's roster review).
    """
    documents = await gather_source_documents(drive_client, github_client, user_id, settings)
    confirmed: dict[str, Experience] = {}
    for proposal in HeuristicRosterProposer().propose(documents):
        experience = repository.propose_experience(user_id, proposal.to_seed())
        confirmed[proposal.name.casefold()] = repository.set_experience_status(
            experience.id, ExperienceStatus.CONFIRMED
        )
    for document in documents:
        entity = confirmed.get(document.title.casefold())
        target = entity.id if entity is not None else None
        if document.source_type == SOURCE_GITHUB_COMMIT:
            stored = repository.upsert_evidence(
                user_id, EvidenceChunk(document.source_type, document.source_ref, document.text)
            )
            repository.assign_evidence(stored.id, target)
            continue
        for piece in chunk_normalized_text(document.text):
            stored = repository.upsert_evidence(
                user_id,
                EvidenceChunk(
                    document.source_type,
                    span_ref(document.source_ref, piece.start, piece.end),
                    piece.text,
                ),
            )
            repository.assign_evidence(stored.id, target)
    return confirmed


def seed_approved_story(
    claim_repo: InMemoryClaimRepository,
    story_repo: InMemoryProjectStoryRepository,
    *,
    user_id: str = "u1",
    name: str = "Ledgerline",
    topic: str = "settlement",
    section: ExperienceSection = ExperienceSection.PROFESSIONAL_EXPERIENCE,
    approve: bool = True,
) -> Experience:
    """Seed one confirmed entity with a resume-ready, (optionally) approved project story.

    The V3 pipeline reads approved *stories*, not claims, so pipeline/scheduler tests need a
    complete P-A-R story: a problem-space Problem, ≥1 Action, and an evidenced Result whose
    number is grounded in its cited chunk. ``topic`` keeps distinct seeded stories from
    sharing an outcome span (which would trip the duplicate-metric refusal).
    """
    experience = claim_repo.upsert_experience(user_id, ExperienceSeed(name=name, section=section))
    problem = f"The {topic} team spent 9 hours a week reconciling ledgers by hand"
    action_one = f"Re-architected the {topic} pipeline over Kafka in Python"
    result_one = f"Cut {topic} batch runtime by 70%"
    action_two = f"Built streaming {topic} reconciliation services with SQL and Python"

    full_chunk = EvidenceChunk(
        SOURCE_DRIVE, f"drv-{topic}-1", "\n".join((problem, action_one, result_one))
    )
    action_chunk = EvidenceChunk(SOURCE_DRIVE, f"drv-{topic}-2", action_two)
    storables = [
        StorableClaim(
            draft=DraftClaim(
                action_text=action_one,
                action_tools=("Python", "Kafka"),
                problem_text=problem,
                problem_cost_dimension=CostDimension.TIME,
                problem_inefficiency=Inefficiency.MANUAL,
                result_text=result_one,
                result_kind=ResultKind.QUANTIFIED,
                result_metric_json={"resolves": "time", "metric_text": "70%"},
                evidence=(
                    ClaimEvidenceRef(chunk=full_chunk, field=ClaimField.PROBLEM),
                    ClaimEvidenceRef(chunk=full_chunk, field=ClaimField.ACTION),
                    ClaimEvidenceRef(
                        chunk=full_chunk, field=ClaimField.RESULT, outcome_quote=result_one
                    ),
                ),
            ),
            status=ClaimStatus.PENDING_REVIEW,
            result_status=ResultStatus.VERIFIED,
        ),
        StorableClaim(
            draft=DraftClaim(
                action_text=action_two,
                action_tools=("SQL", "Python"),
                evidence=(ClaimEvidenceRef(chunk=action_chunk, field=ClaimField.ACTION),),
            ),
            status=ClaimStatus.PENDING_REVIEW,
            result_status=ResultStatus.UNVERIFIED,
        ),
    ]
    claim_repo.replace_unreviewed_claims(user_id, experience.id, storables)

    claims = [c for c in claim_repo.list_claims(user_id) if c.experience_id == experience.id]
    content = select_story_content(experience.id, claims)
    story = story_repo.upsert_draft(user_id, experience.id, content)
    story_repo.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    if approve:
        story_repo.transition_story(story.id, StoryReviewStatus.APPROVED)
    return experience


@pytest.fixture
def drive_fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def github_fixtures_dir() -> Path:
    return GITHUB_FIXTURES_DIR


@pytest.fixture
def settings() -> Settings:
    """Safe-path settings scoped to the fixture career-docs folder and GitHub account.

    Constructed explicitly (not from the environment) so tests never depend on a real
    ``.env`` or credentials.
    """
    return Settings(
        gdrive_mcp_enabled=False,
        gdrive_source_folder_id=APPROVED_FOLDER_ID,
        gdrive_mock_fixtures_dir=str(FIXTURES_DIR),
        gdrive_allow_broad_scan=False,
        github_mcp_enabled=False,
        github_username=GITHUB_USERNAME,
        github_mock_fixtures_dir=str(GITHUB_FIXTURES_DIR),
        github_allow_broad_scan=False,
        jobs_mock_fixtures_dir=str(JOBS_FIXTURES_DIR),
        research_mock_fixtures_dir=str(RESEARCH_FIXTURES_DIR),
        inbox_mock_fixtures_dir=str(INBOX_FIXTURES_DIR),
    )


@pytest.fixture
def mock_client(drive_fixtures_dir: Path) -> MockDriveClient:
    return MockDriveClient(drive_fixtures_dir)


@pytest.fixture
def mock_github_client(github_fixtures_dir: Path) -> MockGitHubClient:
    return MockGitHubClient(github_fixtures_dir)


@pytest.fixture
def mock_job_source() -> MockJobSource:
    return MockJobSource(JOBS_FIXTURES_DIR)


@pytest.fixture
def mock_research_client() -> MockResearchClient:
    return MockResearchClient(RESEARCH_FIXTURES_DIR)
