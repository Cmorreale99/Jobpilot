"""Nightly application pipeline on mocks: end-to-end flow and idempotent re-runs.

V2: the pipeline's Master CV is a snapshot of APPROVED claims — the tests seed a claim
repository, approve claims, and verify that matching/tailoring/outreach run from that
ledger only (and refuse to run from an empty one).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.domain.applications import ApplicationStatus, OutreachStatus
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
from app.integrations.mock.jobs import MockJobSource
from app.integrations.mock.mail import MockMailClient
from app.integrations.mock.research import MockResearchClient
from app.services.application_repository import InMemoryApplicationRepository
from app.services.claim_repository import InMemoryClaimRepository
from app.services.job_repository import InMemoryJobRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from app.services.pipeline import PipelineDependencies, run_application_pipeline

from tests.conftest import JOBS_FIXTURES_DIR, RESEARCH_FIXTURES_DIR

# Fixture jobs are posted late June 2026; this window includes them all.
_NOW = datetime(2026, 7, 1, 2, 0, tzinfo=UTC)

_CHUNK = EvidenceChunk(SOURCE_DRIVE, "drv1", "Rebuilt the settlement pipeline in Python.")


def _storable(action: str, result: str | None = None) -> StorableClaim:
    return StorableClaim(
        draft=DraftClaim(
            action_text=action,
            action_tools=("Python",),
            result_text=result,
            result_kind=ResultKind.QUALITATIVE_EVIDENCED if result else ResultKind.MISSING,
            evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
        ),
        status=ClaimStatus.PENDING_REVIEW,
        result_status=ResultStatus.UNVERIFIED,
    )


def _experience_seed(name: str) -> ExperienceSeed:
    return ExperienceSeed(name=name, section=ExperienceSection.PROFESSIONAL_EXPERIENCE)


def _seed_approved_claims(repo: InMemoryClaimRepository) -> None:
    experience = repo.upsert_experience("u1", _experience_seed("Ledgerline"))
    inserted = repo.replace_unreviewed_claims(
        "u1",
        experience.id,
        [
            _storable(
                "Re-architected the settlement pipeline over Kafka in Python",
                result="Cut batch runtime by 70%",
            ),
            _storable("Built streaming payment reconciliation services with SQL and Python"),
        ],
    )
    for claim in inserted:
        repo.transition_claim(claim.id, ClaimStatus.APPROVED)


@pytest.fixture
def deps() -> PipelineDependencies:
    claim_repository = InMemoryClaimRepository()
    _seed_approved_claims(claim_repository)
    return PipelineDependencies(
        job_source=MockJobSource(JOBS_FIXTURES_DIR),
        research_client=MockResearchClient(RESEARCH_FIXTURES_DIR),
        mail_client=MockMailClient(),
        claim_repository=claim_repository,
        snapshot_store=InMemorySnapshotStore(),
        job_repository=InMemoryJobRepository(),
        application_repository=InMemoryApplicationRepository(),
    )


@pytest.fixture
def pipeline_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"top_n": 4, "shortlist_size": 10, "jobs_since_hours": 24 * 30}
    )


async def test_pipeline_runs_end_to_end(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    result = await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    assert result.user_id == "u1"
    assert result.master_cv_version == 1
    assert result.master_cv_changed is True
    assert result.match_count == 4  # top_n
    assert result.drafts_queued == 4

    # Everything landed where the dashboard reads it.
    assert deps.job_repository.get_matches("u1", 1)
    queue = deps.application_repository.list_pending_outreach("u1")
    assert len(queue) == 4
    applications = deps.application_repository.list_applications("u1")
    assert all(a.status is ApplicationStatus.DRAFTED for a in applications)
    assert all(a.materials.highlights for a in applications)
    # Every highlight is a verbatim rendering of an APPROVED claim — nothing invented,
    # nothing from the retired V1 path — and carries that claim's ledger id (the
    # Phase 3 exit test: prose that leaves the machine is claim-traceable).
    approved = deps.claim_repository.list_claims("u1", status=ClaimStatus.APPROVED)
    approved_actions = {c.action_text for c in approved}
    approved_ids = {c.id for c in approved}
    for application in applications:
        materials = application.materials
        assert len(materials.highlight_claim_ids) == len(materials.highlights)
        assert set(materials.highlight_claim_ids) <= approved_ids
        for highlight in materials.highlights:
            assert any(highlight.startswith(action) for action in approved_actions)


async def test_no_approved_claims_means_no_matching_and_no_drafts(
    pipeline_settings: Settings,
) -> None:
    # An unreviewed ledger (claims exist but none approved) generates NOTHING.
    claim_repository = InMemoryClaimRepository()
    experience = claim_repository.upsert_experience("u1", _experience_seed("Ledgerline"))
    claim_repository.replace_unreviewed_claims(
        "u1", experience.id, [_storable("Pending, unreviewed claim text")]
    )
    deps = PipelineDependencies(
        job_source=MockJobSource(JOBS_FIXTURES_DIR),
        research_client=MockResearchClient(RESEARCH_FIXTURES_DIR),
        mail_client=MockMailClient(),
        claim_repository=claim_repository,
        snapshot_store=InMemorySnapshotStore(),
        job_repository=InMemoryJobRepository(),
        application_repository=InMemoryApplicationRepository(),
    )

    result = await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    assert result.match_count == 0
    assert result.drafts_queued == 0
    assert result.drafts_sent == 0
    assert deps.application_repository.list_applications("u1") == []
    assert deps.job_repository.get_matches("u1", result.master_cv_version) == []


async def test_rerun_is_fully_idempotent(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    first = await run_application_pipeline(deps, pipeline_settings, now=_NOW)
    second = await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    # Unchanged approved claims -> same CV version, no new rows anywhere.
    assert second.master_cv_version == first.master_cv_version
    assert second.master_cv_changed is False
    assert deps.snapshot_store.list_versions("u1") == [1]
    assert len(deps.application_repository.list_applications("u1")) == first.drafts_queued
    first_queue = deps.application_repository.list_pending_outreach("u1")
    assert len(first_queue) == first.drafts_queued  # replaced in place, not duplicated


async def test_newly_approved_claim_cuts_a_new_version(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    first = await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    experience = deps.claim_repository.list_experiences("u1")[0]
    (inserted,) = deps.claim_repository.replace_unreviewed_claims(
        "u1", experience.id, [_storable("Migrated the ledger exports to Snowflake")]
    )
    deps.claim_repository.transition_claim(inserted.id, ClaimStatus.APPROVED)
    second = await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    assert second.master_cv_version == first.master_cv_version + 1
    assert second.master_cv_changed is True


async def test_rerun_preserves_human_decisions(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    await run_application_pipeline(deps, pipeline_settings, now=_NOW)
    queue = deps.application_repository.list_pending_outreach("u1")
    approved = deps.application_repository.transition_outreach(queue[0].id, OutreachStatus.APPROVED)
    application = deps.application_repository.list_applications("u1")[1]
    deps.application_repository.transition_application(application.id, ApplicationStatus.APPLIED)

    await run_application_pipeline(deps, pipeline_settings, now=_NOW)

    still_approved = deps.application_repository.get_outreach(approved.id)
    assert still_approved is not None and still_approved.status is OutreachStatus.APPROVED
    reread = deps.application_repository.get_application(application.id)
    assert reread is not None and reread.status is ApplicationStatus.APPLIED
    # The approved draft left the queue. The applied application's own draft stays
    # pending — applying does not silently discard an undecided outreach message.
    assert len(deps.application_repository.list_pending_outreach("u1")) == 3


async def test_fresh_jobs_window_is_honored(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    # A `since` after every fixture posting -> nothing fetched, nothing drafted.
    result = await run_application_pipeline(
        deps, pipeline_settings, since=_NOW + timedelta(days=1), now=_NOW
    )
    assert result.match_count == 0
    assert result.drafts_queued == 0
    assert result.master_cv_version == 1  # the snapshot was still cut


async def test_auto_send_approves_and_sends_addressable_drafts(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    auto = pipeline_settings.model_copy(update={"outreach_auto_send": True})
    result = await run_application_pipeline(deps, auto, now=_NOW)

    # Fixture contacts: Ledgerline + Sentinel Pay have emails; Streamforge has a
    # contact without one; the rest have no researched contact at all.
    mail = deps.mail_client
    assert isinstance(mail, MockMailClient)
    assert result.drafts_sent == len(mail.outbox) == 2
    assert {e.to for e in mail.outbox} == {
        "priya.raman@ledgerline.example",
        "m.webb@sentinelpay.example",
    }
    # Everything auto-approved; unaddressable drafts stay approved, never guessed at.
    assert deps.application_repository.list_pending_outreach("u1") == []
    approved = deps.application_repository.list_outreach_by_status("u1", OutreachStatus.APPROVED)
    assert len(approved) == 2
    assert all(r.contact is None or not r.contact.email for r in approved)


async def test_auto_send_rerun_never_resends(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    auto = pipeline_settings.model_copy(update={"outreach_auto_send": True})
    first = await run_application_pipeline(deps, auto, now=_NOW)
    second = await run_application_pipeline(deps, auto, now=_NOW)

    mail = deps.mail_client
    assert isinstance(mail, MockMailClient)
    assert first.drafts_sent == 2
    assert second.drafts_sent == 0
    assert len(mail.outbox) == 2  # a re-run never re-sends


async def test_flag_off_sends_nothing(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    result = await run_application_pipeline(deps, pipeline_settings, now=_NOW)
    mail = deps.mail_client
    assert isinstance(mail, MockMailClient)
    assert result.drafts_sent == 0
    assert mail.outbox == []
    # All drafts wait in the approval queue for the human.
    assert len(deps.application_repository.list_pending_outreach("u1")) == result.drafts_queued


async def test_default_window_comes_from_settings(
    deps: PipelineDependencies, pipeline_settings: Settings
) -> None:
    # 24h window from _NOW covers only the postings from the last day of June.
    narrow = pipeline_settings.model_copy(update={"jobs_since_hours": 24})
    result = await run_application_pipeline(deps, narrow, now=_NOW)
    wide = await run_application_pipeline(
        deps, pipeline_settings, now=_NOW
    )  # 30-day window sees more
    assert result.match_count <= wide.match_count
