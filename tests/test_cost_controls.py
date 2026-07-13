"""Cost controls (MASTER CV REPAIR §5.8): assignment fingerprint skip + the
fail-closed extraction cost ceiling.

Live finding 2026-07-13: three interrupted runs re-paid the full LLM assignment
pass (~$18 of ~$30), and nothing bounded the run — the credit balance itself was
the stop condition. Unchanged paid semantic work must never repeat (§5.8.1), and
cost is bounded BEFORE execution (§5.8.7).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.domain.claims import (
    SOURCE_DRIVE,
    EvidenceChunk,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
)
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_extraction import estimate_group_cost_usd, run_claim_extraction
from app.services.claim_repository import InMemoryClaimRepository
from app.services.roster import run_roster_assignment
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.test_github_universe import EmptyDriveClient

FIXTURES = Path(__file__).parent / "fixtures" / "github_universe"
USER = "u1"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "github_mcp_enabled": False,
        "github_username": "cmorreale",
        "github_mock_fixtures_dir": str(FIXTURES),
        "gdrive_source_folder_id": "",
        "uploads_dir": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class CountingSectionAssigner:
    """Heuristic-shaped section assigner that counts invocations."""

    method = "heuristic"
    calls = 0

    def assign_sections(self, sections, roster):  # type: ignore[no-untyped-def]
        type(self).calls += 1
        return [None] * len(sections)


# --- §5.8.1: unchanged assignment work is never repeated --------------------------------


@pytest.mark.asyncio
async def test_unchanged_documents_skip_reassignment_entirely() -> None:
    repo = InMemoryClaimRepository()
    log = InMemoryValidationRunLog()
    repo.upsert_experience(
        USER,
        ExperienceSeed(
            name="jobpilot",
            section=ExperienceSection.PROJECTS_HACKATHONS,
            kind=ExperienceKind.PROJECT,
            aliases=("cmorreale/jobpilot",),
        ),
    )
    github = MockGitHubClient(FIXTURES)
    assigner = CountingSectionAssigner()
    CountingSectionAssigner.calls = 0

    first = await run_roster_assignment(
        EmptyDriveClient(),
        github,
        USER,
        repo,
        _settings(),
        section_assigner=assigner,
        validation_log=log,
    )
    calls_after_first = CountingSectionAssigner.calls
    assert calls_after_first > 0
    assert first.skipped_unchanged == 0

    second = await run_roster_assignment(
        EmptyDriveClient(),
        github,
        USER,
        repo,
        _settings(),
        section_assigner=assigner,
        validation_log=log,
    )
    assert CountingSectionAssigner.calls == calls_after_first, (
        "an unchanged document must not re-invoke the (paid) assigner"
    )
    assert second.skipped_unchanged > 0

    # A roster change invalidates the fingerprints: work re-runs.
    repo.upsert_experience(
        USER,
        ExperienceSeed(
            name="Brand New Entity",
            section=ExperienceSection.PROJECTS_HACKATHONS,
            kind=ExperienceKind.PROJECT,
        ),
    )
    third = await run_roster_assignment(
        EmptyDriveClient(),
        github,
        USER,
        repo,
        _settings(),
        section_assigner=assigner,
        validation_log=log,
    )
    assert CountingSectionAssigner.calls > calls_after_first, (
        "a roster change must re-run assignment"
    )
    assert third.skipped_unchanged == 0


# --- §5.8.7: the fail-closed extraction cost ceiling -------------------------------------


class CountingExtractor:
    """Free stand-in that records which groups actually started."""

    def __init__(self) -> None:
        self.groups: list[str] = []

    def extract(self, group, violations=()):  # type: ignore[no-untyped-def]
        self.groups.append(group.experience.name)
        return []


def _seed_group(repo: InMemoryClaimRepository, name: str, chars: int) -> None:
    experience = repo.upsert_experience(
        USER,
        ExperienceSeed(
            name=name,
            section=ExperienceSection.PROJECTS_HACKATHONS,
            kind=ExperienceKind.PROJECT,
        ),
    )
    stored = repo.upsert_evidence(USER, EvidenceChunk(SOURCE_DRIVE, f"doc-{name}", "x" * chars))
    repo.assign_evidence(stored.id, experience.id, method="heuristic")


@pytest.mark.asyncio
async def test_cost_ceiling_stops_before_starting_over_budget_groups() -> None:
    repo = InMemoryClaimRepository()
    # Each ~400K-char group estimates ≈ $7.35 on sonnet pricing (calibrated envelope);
    # an $8 ceiling admits exactly one.
    _seed_group(repo, "Alpha", 400_000)
    _seed_group(repo, "Beta", 400_000)
    extractor = CountingExtractor()
    settings = _settings(llm_enabled=True, claims_llm_extraction=True, llm_cost_ceiling_usd=8.0)

    report = await run_claim_extraction(USER, repo, settings, extractor=extractor)

    assert len(extractor.groups) == 1, "only the affordable group may start"
    assert len(report.skipped_budget) == 1
    assert report.estimated_cost_usd <= 8.0
    # The skipped group stays eligible: a second (higher-ceiling) run picks it up
    # and the completed one is hash-skipped, not re-paid.
    settings2 = _settings(llm_enabled=True, claims_llm_extraction=True, llm_cost_ceiling_usd=50.0)
    report2 = await run_claim_extraction(USER, repo, settings2, extractor=extractor)
    assert not report2.skipped_budget
    assert len(report2.skipped_unchanged) == 1
    assert sorted(set(extractor.groups)) == ["Alpha", "Beta"]


@pytest.mark.asyncio
async def test_ceiling_never_gates_the_free_heuristic_path() -> None:
    repo = InMemoryClaimRepository()
    _seed_group(repo, "Gamma", 400_000)
    extractor = CountingExtractor()
    settings = _settings(llm_cost_ceiling_usd=0.01)  # llm flags OFF: extraction is free

    report = await run_claim_extraction(USER, repo, settings, extractor=extractor)
    assert extractor.groups == ["Gamma"]
    assert not report.skipped_budget


def test_estimate_is_in_a_sane_ballpark() -> None:
    from app.domain.claims import EvidenceGroup

    group = EvidenceGroup(
        experience=ExperienceSeed(name="x", section=ExperienceSection.PROJECTS_HACKATHONS),
        chunks=(EvidenceChunk(SOURCE_DRIVE, "d", "y" * 100_000),),
    )
    est = estimate_group_cost_usd(group, "claude-sonnet-5")
    # 25K tokens → calibrated envelope ≈ $1.84; must be neither $0 nor absurd.
    assert 0.5 < est < 5.0
