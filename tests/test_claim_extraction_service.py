"""End-to-end claim extraction over the mock clients and V2 claims fixtures.

Runs the full M8 slice offline: confirmed roster (via ``confirm_source_roster``) ->
two-pass extraction -> PAR validation -> bounce-once -> persisted pending_review
claims. Also exercises the bounce loop in isolation with scripted extractors.
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
    ClaimStatus,
    CostDimension,
    DraftClaim,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    Inefficiency,
    ResultKind,
    ResultStatus,
)
from app.domain.par_validation import COUPLING_FLAG
from app.integrations.mock.drive import MockDriveClient
from app.integrations.mock.github import MockGitHubClient
from app.services.claim_extraction import (
    extract_and_validate_group,
    run_claim_extraction,
)
from app.services.claim_repository import InMemoryClaimRepository

from tests.conftest import confirm_source_roster

CLAIMS_FIXTURES = Path(__file__).parent / "fixtures" / "claims"


@pytest.fixture
def claims_settings() -> Settings:
    return Settings(
        gdrive_mcp_enabled=False,
        gdrive_source_folder_id="career_docs",
        gdrive_mock_fixtures_dir=str(CLAIMS_FIXTURES / "drive"),
        gdrive_allow_broad_scan=False,
        github_mcp_enabled=False,
        github_username="jordanrivera",
        github_mock_fixtures_dir=str(CLAIMS_FIXTURES / "github"),
        github_allow_broad_scan=False,
    )


@pytest.fixture
def drive_client() -> MockDriveClient:
    return MockDriveClient(CLAIMS_FIXTURES / "drive")


@pytest.fixture
def github_client() -> MockGitHubClient:
    return MockGitHubClient(CLAIMS_FIXTURES / "github")


# --- mock client: commits -------------------------------------------------------------


async def test_mock_github_client_lists_fixture_commits(
    github_client: MockGitHubClient,
) -> None:
    commits = await github_client.list_commits("jordanrivera/carrier-etl")
    assert [c.sha for c in commits] == ["c001workonly", "c002outcome"]
    assert all(c.repo_ref == "jordanrivera/carrier-etl" for c in commits)
    assert commits[0].authored_at is not None


async def test_mock_github_client_returns_no_commits_when_manifest_has_none() -> None:
    client = MockGitHubClient(Path(__file__).parent / "fixtures" / "github")
    assert await client.list_commits("jordanrivera/fraud-stream") == []


# --- the full fixture loop --------------------------------------------------------------


async def test_full_extraction_loop_persists_pending_claims(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    claims_settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    await confirm_source_roster(drive_client, github_client, "u1", repo, claims_settings)
    report = await run_claim_extraction("u1", repo, claims_settings)

    # Experiences: two Drive docs (professional) + one repo (projects), all confirmed
    # by the roster bootstrap — extraction itself creates nothing (Phase 0).
    experiences = {e.name: e for e in repo.list_experiences("u1")}
    assert set(experiences) == {
        "Shipment Reconciliation Writeup",
        "CI Throughput Project",
        "carrier-etl",
    }
    assert (
        experiences["Shipment Reconciliation Writeup"].section
        is ExperienceSection.PROFESSIONAL_EXPERIENCE
    )
    assert experiences["carrier-etl"].section is ExperienceSection.PROJECTS_HACKATHONS

    # Extraction NEVER produces an approved claim.
    assert report.claims
    assert all(c.status is ClaimStatus.PENDING_REVIEW for c in report.claims)

    by_action = {c.action_text: c for c in report.claims}

    # The clean writeup passes every gate: quantified, verified, unflagged.
    recon = by_action["Automated exception triage with Python and SQL rules over the carrier feed."]
    assert recon.validation_flags == ()
    assert recon.result_kind is ResultKind.QUANTIFIED
    assert recon.result_status is ResultStatus.VERIFIED
    assert recon.problem_cost_dimension is CostDimension.TIME
    assert recon.problem_inefficiency is Inefficiency.MANUAL

    # The CI writeup trips the coupling rule (slow Problem, revenue Result).
    ci = by_action["Parallelized the integration suite with GitHub Actions matrix builds."]
    assert any(COUPLING_FLAG in flag for flag in ci.validation_flags)
    assert ci.result_status is ResultStatus.UNVERIFIED

    # EXIT: the work-statement-only commit yields result_kind=missing, never a Result.
    dedup = by_action["Fixed dedup key mismatch across four carriers in the Kafka ingest path"]
    assert dedup.result_kind is ResultKind.MISSING
    assert dedup.result_text is None
    assert dedup in report.missing_results

    # EXIT: the outcome commit yields quantified with the verbatim metric.
    backfill = by_action["Backfilled the missing upstream dataset behind nightly scoring"]
    assert backfill.result_kind is ResultKind.QUANTIFIED
    assert backfill.result_metric_json is not None
    assert (
        backfill.result_metric_json["metric_text"]
        == "Cut ingestion failure rate from 12% to 0.5% across the Python scoring jobs."
    )

    # Every claim traces to evidence; result links carry their outcome quote.
    for claim in report.claims:
        assert claim.evidence
        for link in claim.evidence:
            assert repo.get_evidence(link.evidence_id) is not None
            if link.field is ClaimField.RESULT:
                assert link.outcome_quote

    # Evidence rows exist for all three source types.
    source_types = set()
    for claim in report.claims:
        for link in claim.evidence:
            evidence = repo.get_evidence(link.evidence_id)
            assert evidence is not None
            source_types.add(evidence.source_type)
    assert {SOURCE_DRIVE, SOURCE_GITHUB_COMMIT, SOURCE_GITHUB_README} >= source_types
    assert SOURCE_DRIVE in source_types
    assert SOURCE_GITHUB_COMMIT in source_types


async def test_rerun_is_idempotent_and_preserves_review_decisions(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    claims_settings: Settings,
) -> None:
    repo = InMemoryClaimRepository()
    await confirm_source_roster(drive_client, github_client, "u1", repo, claims_settings)
    first = await run_claim_extraction("u1", repo, claims_settings)

    approved = repo.transition_claim(first.claims[0].id, ClaimStatus.APPROVED)

    second = await run_claim_extraction("u1", repo, claims_settings)

    all_claims = repo.list_claims("u1")
    assert len(all_claims) == len(first.claims)  # no duplicates on re-run
    assert repo.get_claim(approved.id).status is ClaimStatus.APPROVED  # type: ignore[union-attr]
    # The approved claim's content was not re-queued.
    assert all(c.id != approved.id for c in second.claims)


# --- the bounce loop ---------------------------------------------------------------------


_CHUNK = EvidenceChunk(
    SOURCE_DRIVE,
    "drv_x",
    "Problem: Manual reconciliation took 10 hours per week.\n"
    "Action: Automated exception triage with Python.",
)
_GROUP = EvidenceGroup(
    experience=ExperienceSeed(name="x", section=ExperienceSection.PROFESSIONAL_EXPERIENCE),
    chunks=(_CHUNK,),
)

_GOOD = DraftClaim(
    action_text="Automated exception triage with Python",
    action_tools=("Python",),
    problem_text="Manual reconciliation took 10 hours per week.",
    problem_cost_dimension=CostDimension.TIME,
    problem_inefficiency=Inefficiency.MANUAL,
    evidence=(
        # action + problem cite the chunk; result stays honestly missing
        ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),
        ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.PROBLEM),
    ),
)

# Advisory-bad: long enough to be reviewable, but no problem and no tools declared.
_BAD = DraftClaim(
    action_text="Automated exception triage across the carrier feeds", action_tools=()
)


class _ScriptedExtractor:
    """Fails validation on the first pass, corrects itself when given the violations."""

    def __init__(self, always_bad: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._always_bad = always_bad

    def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
        self.calls.append(tuple(violations))
        if self._always_bad or not violations:
            return [_BAD]
        return [_GOOD]


def test_validator_failure_bounces_to_reextraction_once_with_the_violations() -> None:
    extractor = _ScriptedExtractor()
    extraction = extract_and_validate_group(extractor, _GROUP)

    assert len(extractor.calls) == 2
    assert extractor.calls[0] == ()
    assert any("problem_missing" in v for v in extractor.calls[1])
    assert any("action_names_no_tools" in v for v in extractor.calls[1])

    assert len(extraction.storables) == 1
    assert extraction.storables[0].validation_flags == ()
    assert extraction.storables[0].status is ClaimStatus.PENDING_REVIEW


def test_second_failure_lands_in_the_queue_flagged() -> None:
    extractor = _ScriptedExtractor(always_bad=True)
    extraction = extract_and_validate_group(extractor, _GROUP)

    assert len(extractor.calls) == 2  # bounce happens exactly once
    assert len(extraction.storables) == 1
    flagged = extraction.storables[0]
    assert flagged.status is ClaimStatus.PENDING_REVIEW
    # Only the INTEGRITY violation persists as a flag; the missing problem is
    # readiness data, not a claim defect (Phase 0 flag split).
    assert any("action_names_no_tools" in flag for flag in flagged.validation_flags)
    assert not any("problem_missing" in flag for flag in flagged.validation_flags)
    assert flagged.result_status is ResultStatus.UNVERIFIED


async def test_par_validator_runs_are_logged(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    claims_settings: Settings,
) -> None:
    from app.domain.validation_runs import KIND_PAR_VALIDATION
    from app.services.validation_run_log import InMemoryValidationRunLog

    repo = InMemoryClaimRepository()
    await confirm_source_roster(drive_client, github_client, "u1", repo, claims_settings)
    log = InMemoryValidationRunLog()
    report = await run_claim_extraction("u1", repo, claims_settings, validation_log=log)

    runs = log.list_runs("u1", KIND_PAR_VALIDATION)
    assert len(runs) == len(report.claims)  # one auditable row per persisted claim
    by_subject = {run.subject_ref: run for run in runs}
    for claim in report.claims:
        run = by_subject[f"claim:{claim.id}"]
        assert run.passed is (not claim.validation_flags)
        assert run.detail == claim.validation_flags


def test_clean_extraction_does_not_bounce() -> None:
    class _CleanExtractor:
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
            self.calls += 1
            return [_GOOD]

    extractor = _CleanExtractor()
    extraction = extract_and_validate_group(extractor, _GROUP)
    assert extractor.calls == 1
    assert extraction.storables[0].validation_flags == ()
    # An honest missing Result is unverified until review resolves it.
    assert extraction.storables[0].result_status is ResultStatus.UNVERIFIED


def test_absence_violations_neither_bounce_nor_flag() -> None:
    """A problem the evidence never stated cannot be re-extracted into existence —
    bouncing on it doubled the LLM cost of every commit-heavy group for nothing.
    And it is readiness data, not a claim defect: it never persists as a flag, so
    it never blocks approve-as-is (Phase 0 flag split)."""

    class _Counting:
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
            self.calls += 1
            return [
                DraftClaim(
                    action_text="Automated exception triage with Python",
                    action_tools=("Python",),
                    evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
                )
            ]

    extractor = _Counting()
    extraction = extract_and_validate_group(extractor, _GROUP)
    assert extractor.calls == 1  # problem_missing alone never triggers the bounce
    (storable,) = extraction.storables
    assert storable.validation_flags == ()  # absence is not a flag
    assert storable.draft.problem_text is None  # ...and stays recomputable from the claim


async def test_unchanged_groups_are_skipped_and_force_reextracts(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    claims_settings: Settings,
) -> None:
    class _Counting:
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
            self.calls += 1
            return [
                DraftClaim(
                    action_text=f"Automated triage batch {self.calls} with Python",
                    action_tools=("Python",),
                    evidence=(ClaimEvidenceRef(chunk=group.chunks[0], field=ClaimField.ACTION),),
                )
            ]

    repo = InMemoryClaimRepository()
    await confirm_source_roster(drive_client, github_client, "u1", repo, claims_settings)
    extractor = _Counting()
    first = await run_claim_extraction("u1", repo, claims_settings, extractor=extractor)
    calls_after_first = extractor.calls
    assert first.claims and first.skipped_unchanged == []

    second = await run_claim_extraction("u1", repo, claims_settings, extractor=extractor)
    assert extractor.calls == calls_after_first  # zero extraction calls on the re-run
    assert len(second.skipped_unchanged) == 3  # all three fixture groups
    assert second.claims == []
    assert len(repo.list_claims("u1")) == len(first.claims)  # queue untouched

    forced = await run_claim_extraction(
        "u1", repo, claims_settings, extractor=extractor, force=True
    )
    assert extractor.calls > calls_after_first
    assert forced.skipped_unchanged == []


# --- Phase 1: structural drops, dedupe, loud failure ---------------------------------------


class _FixedExtractor:
    """Always returns the given drafts (deterministic across the bounce)."""

    def __init__(self, drafts: list[DraftClaim]) -> None:
        self._drafts = drafts

    def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
        return list(self._drafts)


def test_one_word_problem_is_dropped_never_persisted() -> None:
    """Audit test case 1: 'manual' as a Problem is structurally invalid."""
    draft = DraftClaim(
        action_text="Designed and implemented a RAG system with Python",
        action_tools=("Python",),
        problem_text="manual",
        problem_inefficiency=Inefficiency.MANUAL,
        evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
    )
    extraction = extract_and_validate_group(_FixedExtractor([draft]), _GROUP)
    assert extraction.storables == []
    ((dropped_draft, violations),) = extraction.dropped
    assert dropped_draft.problem_text == "manual"
    assert any("problem_not_specific" in v for v in violations)


def test_fragment_action_is_dropped_never_persisted() -> None:
    for fragment in ("design,", "extracted", "Reconstructed the data architecture and"):
        draft = DraftClaim(
            action_text=fragment,
            action_tools=(),
            evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
        )
        extraction = extract_and_validate_group(_FixedExtractor([draft]), _GROUP)
        assert extraction.storables == [], f"fragment queued: {fragment!r}"
        assert any("action_fragment" in v for _, vs in extraction.dropped for v in vs)


async def test_dropped_claims_never_reach_the_repository(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    claims_settings: Settings,
) -> None:
    from app.services.validation_run_log import InMemoryValidationRunLog

    bad = DraftClaim(
        action_text="extracted",
        action_tools=(),
        evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
    )
    repo = InMemoryClaimRepository()
    await confirm_source_roster(drive_client, github_client, "u1", repo, claims_settings)
    log = InMemoryValidationRunLog()
    report = await run_claim_extraction(
        "u1",
        repo,
        claims_settings,
        extractor=_FixedExtractor([bad, _GOOD]),
        validation_log=log,
    )

    actions = {c.action_text for c in repo.list_claims("u1")}
    assert "extracted" not in actions
    assert report.dropped  # reported, not silently discarded
    dropped_runs = [r for r in log.list_runs("u1") if r.subject_ref.startswith("dropped:")]
    assert dropped_runs and all(not r.passed for r in dropped_runs)


async def test_same_content_never_queues_under_two_experiences(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    claims_settings: Settings,
) -> None:
    """Audit: claims 41/42 — one bullet from two resume versions queued twice."""

    class _EchoExtractor:
        """Every group yields the SAME claim content, cited from its own chunk."""

        def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
            return [
                DraftClaim(
                    action_text="Automated exception triage with Python",
                    action_tools=("Python",),
                    evidence=(ClaimEvidenceRef(chunk=group.chunks[0], field=ClaimField.ACTION),),
                )
            ]

    repo = InMemoryClaimRepository()
    await confirm_source_roster(drive_client, github_client, "u1", repo, claims_settings)
    report = await run_claim_extraction(
        "u1",
        repo,
        claims_settings,
        extractor=_EchoExtractor(),  # every group extracts identical content
    )

    assert len(report.claims) == 1  # first group queued it; every other group deduped
    assert report.deduped
    fingerprints = {(c.problem_text, c.action_text, c.result_text) for c in repo.list_claims("u1")}
    assert len(fingerprints) == len(repo.list_claims("u1"))


async def test_extraction_failure_skips_the_group_loudly_and_keeps_existing_claims(
    drive_client: MockDriveClient,
    github_client: MockGitHubClient,
    claims_settings: Settings,
) -> None:
    from app.domain.claims import ClaimExtractionError
    from app.domain.validation_runs import KIND_EXTRACTION_FAILURE
    from app.services.validation_run_log import InMemoryValidationRunLog

    repo = InMemoryClaimRepository()
    await confirm_source_roster(drive_client, github_client, "u1", repo, claims_settings)
    baseline = await run_claim_extraction("u1", repo, claims_settings)
    assert baseline.claims

    class _ExplodingExtractor:
        def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
            raise ClaimExtractionError(f"LLM extraction failed for {group.experience.name!r}")

    log = InMemoryValidationRunLog()
    report = await run_claim_extraction(
        "u1",
        repo,
        claims_settings,
        extractor=_ExplodingExtractor(),
        validation_log=log,
        force=True,  # bypass the unchanged-evidence skip to exercise the failure path
    )

    assert report.claims == []
    assert len(report.failed_groups) == 3  # every group failed, loudly
    failures = log.list_runs("u1", KIND_EXTRACTION_FAILURE)
    assert len(failures) == 3 and all(not r.passed for r in failures)
    # The failed run never touched the claims a previous run queued.
    assert len(repo.list_claims("u1")) == len(baseline.claims)
