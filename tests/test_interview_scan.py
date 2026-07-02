"""Interview scan service on fixtures: detection, idempotency, the privacy gate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.config import Settings
from app.domain.applications import ApplicationStatus
from app.domain.cv import MasterCv, ParClaim
from app.domain.interviews import (
    HeuristicInviteDetector,
    HeuristicPrepPacketGenerator,
    InboxMessage,
    InterviewStage,
)
from app.domain.jobs import Job
from app.domain.tailoring import TailoredMaterials
from app.integrations.mock.inbox import MockInboxScanner
from app.services.application_repository import InMemoryApplicationRepository
from app.services.interview_repository import InMemoryInterviewRepository
from app.services.interview_scan import (
    InterviewScanDependencies,
    run_interview_scan,
)
from app.services.master_cv_repository import InMemoryMasterCvRepository

from tests.conftest import INBOX_FIXTURES_DIR

# All fixture invites arrive late June 2026.
_NOW = datetime(2026, 7, 1, 3, 0, tzinfo=UTC)


class _SpyScanner:
    """Fails the test if any inbox read happens (the privacy-gate check)."""

    def __init__(self) -> None:
        self.calls = 0

    async def search_messages(
        self, query: str, since: datetime | None = None
    ) -> list[InboxMessage]:
        self.calls += 1
        return []


def _deps(scanner: object | None = None) -> InterviewScanDependencies:
    master_cvs = InMemoryMasterCvRepository()
    master_cvs.save(
        "u1",
        MasterCv(
            claims=[
                ParClaim(
                    action="Re-architected the settlement pipeline over Kafka",
                    source_ref="resume",
                    result="Cut runtime by 70%",
                )
            ]
        ),
    )
    applications = InMemoryApplicationRepository()
    job = Job(
        source="mock",
        external_id="job-1001",
        title="Staff Backend Engineer, Payments",
        company="Ledgerline",
        description="Settlement workers over Kafka.",
    )
    application = applications.get_or_create_application(
        "u1",
        job,
        1,
        TailoredMaterials(
            summary="Fit for Staff Backend Engineer at Ledgerline.",
            highlights=("Re-architected the settlement pipeline over Kafka — Cut runtime by 70%",),
            cover_letter="(letter)",
        ),
    )
    applications.transition_application(application.id, ApplicationStatus.APPLIED)
    return InterviewScanDependencies(
        inbox_scanner=scanner or MockInboxScanner(INBOX_FIXTURES_DIR),  # type: ignore[arg-type]
        detector=HeuristicInviteDetector(),
        prep_generator=HeuristicPrepPacketGenerator(),
        interview_repository=InMemoryInterviewRepository(),
        master_cv_repository=master_cvs,
        application_repository=applications,
    )


@pytest.fixture
def scan_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"interview_scan_since_hours": 24 * 30})


async def test_scan_detects_invites_and_generates_packets(scan_settings: Settings) -> None:
    deps = _deps()
    result = await run_interview_scan(deps, scan_settings, now=_NOW)

    assert result.detected == 2  # the two real invites; rejection + digest ignored
    assert result.new_interviews == 2
    assert result.packets_generated == 2
    interviews = deps.interview_repository.list_interviews("u1")
    assert {i.company for i in interviews} == {"Sentinelpay", "Ledgerline"}
    assert all(i.stage is InterviewStage.DETECTED for i in interviews)
    for interview in interviews:
        packet = deps.interview_repository.get_prep_packet(interview.id)
        assert packet is not None and interview.company in packet.content


async def test_packet_links_to_matching_application(scan_settings: Settings) -> None:
    deps = _deps()
    await run_interview_scan(deps, scan_settings, now=_NOW)
    ledgerline = next(
        i for i in deps.interview_repository.list_interviews("u1") if i.company == "Ledgerline"
    )
    packet = deps.interview_repository.get_prep_packet(ledgerline.id)
    assert packet is not None
    # Grounded in the application's tailored materials, not generic filler.
    assert "Fit for Staff Backend Engineer at Ledgerline." in packet.content


async def test_rescan_is_idempotent_and_preserves_stage(scan_settings: Settings) -> None:
    deps = _deps()
    first = await run_interview_scan(deps, scan_settings, now=_NOW)
    scheduled = deps.interview_repository.list_interviews("u1")[0]
    deps.interview_repository.transition_interview(scheduled.id, InterviewStage.SCHEDULED)

    second = await run_interview_scan(deps, scan_settings, now=_NOW)

    assert first.new_interviews == 2
    assert second.new_interviews == 0
    assert second.packets_generated == 0  # existing packets untouched
    assert len(deps.interview_repository.list_interviews("u1")) == 2
    reread = deps.interview_repository.get_interview(scheduled.id)
    assert reread is not None and reread.stage is InterviewStage.SCHEDULED


async def test_flag_off_means_zero_inbox_reads(scan_settings: Settings) -> None:
    spy = _SpyScanner()
    deps = _deps(scanner=spy)
    disabled = scan_settings.model_copy(update={"interview_inbox_scan": False})

    result = await run_interview_scan(deps, disabled, now=_NOW)

    assert spy.calls == 0  # the scanner is never touched
    assert result == type(result)(scanned=0, detected=0, new_interviews=0, packets_generated=0)


async def test_scan_window_excludes_old_mail(scan_settings: Settings) -> None:
    deps = _deps()
    narrow = scan_settings.model_copy(update={"interview_scan_since_hours": 12})
    result = await run_interview_scan(deps, narrow, now=_NOW)
    assert result.scanned == 0  # all fixture mail is older than 12h before _NOW
