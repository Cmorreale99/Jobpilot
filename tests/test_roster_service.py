"""Roster services + roster-mode extraction over reality-shaped fixtures.

The M13 exit tests: detection proposes (idempotently), a human confirms/merges/renames,
assignment scopes chunks (spans + honest unassignment), and extraction runs per
confirmed entity — with the audit's boundary cases: claims land under confirmed
projects (case 5 downstream), cross-project result links are rejected (case 3), and
one outcome span supports at most one claim (case 9).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from app.config import Settings
from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    ClaimEvidenceRef,
    ClaimField,
    DraftClaim,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    ResultKind,
    split_span_ref,
)
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_extraction import (
    DUPLICATE_OUTCOME_FLAG,
    extract_and_validate_group,
    gather_roster_groups,
    run_claim_extraction,
)
from app.services.claim_repository import InMemoryClaimRepository
from app.services.roster import (
    run_roster_assignment,
    run_roster_detection,
)

ROSTER_FIXTURES = Path(__file__).parent / "fixtures" / "roster"


@pytest.fixture
def roster_settings() -> Settings:
    return Settings(
        gdrive_mcp_enabled=False,
        gdrive_source_folder_id="career_docs",
        gdrive_mock_fixtures_dir=str(ROSTER_FIXTURES / "drive"),
        gdrive_allow_broad_scan=False,
        github_mcp_enabled=False,
        github_username="jordanrivera",
        github_mock_fixtures_dir=str(ROSTER_FIXTURES / "github"),
        github_allow_broad_scan=False,
    )


@pytest.fixture
def drive_client() -> MockDriveClient:
    return MockDriveClient(ROSTER_FIXTURES / "drive")


@pytest.fixture
def github_client() -> MockGitHubClient:
    return MockGitHubClient(ROSTER_FIXTURES / "github")


async def _detect(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    settings: Settings,
    repo: InMemoryClaimRepository,
):
    return await run_roster_detection(drive_client, github_client, "u1", repo, settings)


# --- detection ---------------------------------------------------------------------------


async def test_detection_proposes_and_is_idempotent(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    roster_settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    first = await _detect(drive_client, github_client, roster_settings, repo)
    # Heuristic default: one proposal per doc/repo (3 Drive + 1 repo), all proposed.
    assert len(first.proposed) == 4
    assert all(e.status is ExperienceStatus.PROPOSED for e in first.proposed)

    second = await _detect(drive_client, github_client, roster_settings, repo)
    assert second.proposed == []  # nothing new; nothing duplicated
    assert len(repo.list_experiences("u1")) == 4


async def test_discarded_junk_is_never_reproposed(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    roster_settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    report = await _detect(drive_client, github_client, roster_settings, repo)
    junk = next(e for e in report.proposed if e.name == "resume_final_FINAL.pdf")
    repo.set_experience_status(junk.id, ExperienceStatus.DISCARDED)

    again = await _detect(drive_client, github_client, roster_settings, repo)
    assert all(e.id != junk.id for e in again.proposed)
    reread = next(e for e in repo.list_experiences("u1") if e.id == junk.id)
    assert reread.status is ExperienceStatus.DISCARDED


# --- the confirmed-roster flow: rename, merge, assign, extract ---------------------------


async def _reviewed_roster(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    settings: Settings,
    repo: InMemoryClaimRepository,
) -> dict[str, int]:
    """Simulate the human roster review over the fixture proposals.

    The case-study doc's two projects are named explicitly (with aliases the assigner
    can score), the two resume versions merge into one employer entity, and the repo
    proposal is confirmed as-is.
    """
    await _detect(drive_client, github_client, settings, repo)
    by_name = {e.name: e for e in repo.list_experiences("u1")}

    # The multi-project doc: rename its file-shaped proposal to the first project and
    # confirm a second project entity for the other case study.
    wellington = repo.update_experience_details(
        by_name["Data Systems Case Studies (2).pdf"].id,
        name="Wellington platform rebuild",
        aliases=("Wellington",),
    )
    repo.set_experience_status(wellington.id, ExperienceStatus.CONFIRMED)
    pfas = repo.propose_experience(
        "u1",
        ExperienceSeed(
            name="PFAS contamination reporting",
            section=ExperienceSection.PROJECTS_HACKATHONS,
            aliases=("PFAS",),
        ),
    )
    repo.set_experience_status(pfas.id, ExperienceStatus.CONFIRMED)

    # The resume versions: one employer entity, the duplicate merged into it.
    employer = repo.update_experience_details(
        by_name["resume_final (1).pdf"].id,
        name="Coopersmith Data — Data Engineer",
        dates="Jun 2025–Present",
        aliases=("Coopersmith",),
    )
    repo.set_experience_status(employer.id, ExperienceStatus.CONFIRMED)
    repo.merge_experiences(by_name["resume_final_FINAL.pdf"].id, employer.id)

    carrier = by_name["carrier-etl"]
    repo.set_experience_status(carrier.id, ExperienceStatus.CONFIRMED)
    return {
        "wellington": wellington.id,
        "pfas": pfas.id,
        "employer": employer.id,
        "carrier": carrier.id,
    }


async def test_assignment_scopes_chunks_with_spans_and_honest_unassignment(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    roster_settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    ids = await _reviewed_roster(drive_client, github_client, roster_settings, repo)

    report = await run_roster_assignment(drive_client, github_client, "u1", repo, roster_settings)
    assert report.chunks > 0
    assert report.assigned + report.unassigned == report.chunks

    # Repo evidence (README + commit) assigned to the repo's entity by its ref alias.
    carrier_evidence = repo.list_assigned_evidence("u1", ids["carrier"])
    types = {e.source_type for e in carrier_evidence}
    assert SOURCE_GITHUB_README in types and SOURCE_GITHUB_COMMIT in types

    # The multi-project doc's chunks split between the two project entities, and every
    # Drive/README chunk carries an exact char span in its ref.
    wellington_chunks = repo.list_assigned_evidence("u1", ids["wellington"])
    pfas_chunks = repo.list_assigned_evidence("u1", ids["pfas"])
    assert wellington_chunks and pfas_chunks
    assert all("Wellington" in e.chunk_text for e in wellington_chunks)
    assert all("PFAS" in e.chunk_text for e in pfas_chunks)
    for evidence in (*wellington_chunks, *pfas_chunks):
        base, span = split_span_ref(evidence.source_ref)
        assert base == "drv_case_studies" and span is not None

    # The resume evidence landed on the merged employer entity.
    employer_chunks = repo.list_assigned_evidence("u1", ids["employer"])
    assert any("Coopersmith" in e.chunk_text for e in employer_chunks)


async def test_extraction_runs_per_confirmed_entity_and_claims_land_under_projects(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    roster_settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    ids = await _reviewed_roster(drive_client, github_client, roster_settings, repo)
    await run_roster_assignment(drive_client, github_client, "u1", repo, roster_settings)

    report = await run_claim_extraction("u1", repo, roster_settings)

    assert report.claims
    confirmed_ids = set(ids.values())
    assert {c.experience_id for c in report.claims} <= confirmed_ids
    # The word-per-line mangled project produced claims scoped to ITS entity — the
    # boundary the audit demanded, straight from a messy multi-project document.
    wellington_claims = [c for c in report.claims if c.experience_id == ids["wellington"]]
    pfas_claims = [c for c in report.claims if c.experience_id == ids["pfas"]]
    assert wellington_claims and pfas_claims
    assert all(
        "Wellington" in c.action_text or "ingestion" in c.action_text for c in wellington_claims
    )
    # No fragment claims from the mangled text (normalization + structural gates).
    assert all(len(c.action_text.split()) >= 5 for c in report.claims)


# --- boundary structural checks (audit cases 3 and 9) -------------------------------------

_HOME = EvidenceChunk(SOURCE_DRIVE, "drv_a#chars=0-60", "Rebuilt the exporter with Python here.")
_FOREIGN = EvidenceChunk(SOURCE_DRIVE, "drv_b#chars=0-50", "cut costs by 30% somewhere else")
_GROUP = EvidenceGroup(
    experience=ExperienceSeed(name="Entity A", section=ExperienceSection.PROFESSIONAL_EXPERIENCE),
    chunks=(_HOME,),
)


class _Scripted:
    def __init__(self, drafts: list[DraftClaim]) -> None:
        self._drafts = drafts

    def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
        return list(self._drafts)


def test_cross_project_result_link_is_rejected() -> None:
    """Audit test case 3: a Result citing another project's chunk never persists."""
    draft = DraftClaim(
        action_text="Rebuilt the exporter with Python here",
        action_tools=("Python",),
        result_text="cut costs by 30%",
        result_kind=ResultKind.QUALITATIVE_EVIDENCED,
        result_metric_json={"resolves": None},
        evidence=(
            ClaimEvidenceRef(chunk=_HOME, field=ClaimField.ACTION),
            ClaimEvidenceRef(
                chunk=_FOREIGN, field=ClaimField.RESULT, outcome_quote="cut costs by 30%"
            ),
        ),
    )
    extraction = extract_and_validate_group(_Scripted([draft]), _GROUP)
    assert extraction.storables == []
    ((_, violations),) = extraction.dropped
    assert any(v.startswith("result_project_mismatch") for v in violations)


def test_one_outcome_span_supports_at_most_one_claim() -> None:
    """Audit test case 9 (regression for live claims 35/37): no shared Result spans."""
    quote = "Rebuilt the exporter with Python here."

    def claim(action: str) -> DraftClaim:
        return DraftClaim(
            action_text=action,
            action_tools=("Python",),
            result_text=quote,
            result_kind=ResultKind.QUALITATIVE_EVIDENCED,
            result_metric_json={"resolves": None},
            evidence=(
                ClaimEvidenceRef(chunk=_HOME, field=ClaimField.ACTION),
                ClaimEvidenceRef(chunk=_HOME, field=ClaimField.RESULT, outcome_quote=quote),
            ),
        )

    extraction = extract_and_validate_group(
        _Scripted(
            [
                claim("Rebuilt the exporter with Python for the feeds"),
                claim("Migrated the dashboards with Python to the exporter"),
            ]
        ),
        _GROUP,
    )
    assert len(extraction.storables) == 2
    first, second = extraction.storables
    assert first.draft.result_kind is ResultKind.QUALITATIVE_EVIDENCED
    assert second.draft.result_kind is ResultKind.MISSING  # the span was already spent
    assert second.draft.result_text is None
    assert DUPLICATE_OUTCOME_FLAG in second.validation_flags


async def test_targeted_rerun_touches_only_the_named_groups(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    roster_settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    ids = await _reviewed_roster(drive_client, github_client, roster_settings, repo)
    await run_roster_assignment(drive_client, github_client, "u1", repo, roster_settings)
    baseline = await run_claim_extraction("u1", repo, roster_settings)
    assert baseline.claims

    # Restrict the re-run to one entity with an extractor that yields nothing:
    # its unreviewed claims are replaced (emptied); every other entity is untouched.
    report = await run_claim_extraction(
        "u1",
        repo,
        roster_settings,
        extractor=_Scripted([]),
        experience_names=["wellington platform rebuild"],  # case-insensitive
    )
    assert report.claims == []
    remaining = repo.list_claims("u1")
    assert all(c.experience_id != ids["wellington"] for c in remaining)
    untouched = [c for c in baseline.claims if c.experience_id != ids["wellington"]]
    assert {c.id for c in remaining} == {c.id for c in untouched}


async def test_no_confirmed_roster_refuses_extraction(roster_settings: Settings) -> None:
    """Phase 0: the per-file fallback is DELETED — no confirmed roster, no extraction.

    The fallback used to mint CONFIRMED, render-eligible, file-shaped entities with
    zero human review (the V2 audit's root cause). Now the run refuses loudly and
    creates nothing: no experiences, no evidence, no claims.
    """
    repo = InMemoryClaimRepository()
    assert gather_roster_groups("u1", repo) == []
    report = await run_claim_extraction("u1", repo, roster_settings)
    assert report.claims == [] and report.dropped == [] and report.failed_groups == []
    assert repo.list_experiences("u1") == []  # nothing was minted
    assert repo.list_claims("u1") == []
