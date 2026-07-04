"""M10 exit tests: interview hallucination validation.

* A fixture email with a missing ID or a mismatched quote cannot create a record —
  the failure is logged to ``validation_runs`` and nothing is inserted.
* A valid invite lands in the confirm queue only (stage ``detected``, no packet),
  never directly in confirmed/scheduled records.
* The real scheduler refuses to start against mock mail surfaces (mode guard).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.config import Settings
from app.db.session import create_all, create_session_factory
from app.db.validation_run_log import SqlValidationRunLog
from app.domain.interviews import (
    HeuristicInviteDetector,
    HeuristicPrepPacketGenerator,
    InboxMessage,
    InterviewStage,
    verify_invite_provenance,
)
from app.domain.validation_runs import (
    KIND_INTERVIEW_VERIFICATION,
    ValidationRunLog,
)
from app.scheduler import ModeGuardError, assert_mode_guard
from app.services.application_repository import InMemoryApplicationRepository
from app.services.interview_repository import InMemoryInterviewRepository
from app.services.interview_scan import InterviewScanDependencies, run_interview_scan
from app.services.master_cv_repository import InMemoryMasterCvRepository
from app.services.validation_run_log import InMemoryValidationRunLog

_INVITE_BODY = (
    "Hi! We'd like to schedule a 60-minute technical interview next week for the "
    "Senior Engineer role. Please pick a slot."
)


def _message(message_id: str, body: str = _INVITE_BODY) -> InboxMessage:
    return InboxMessage(
        message_id=message_id,
        subject="Interview invitation",
        sender="recruiting@sentinelpay.example",
        body=body,
    )


class _ScriptedScanner:
    """Search returns scripted messages; re-fetch serves from an independent map."""

    def __init__(
        self,
        search_results: list[InboxMessage],
        fetched: dict[str, InboxMessage] | None = None,
    ) -> None:
        self._search_results = search_results
        self._fetched = fetched if fetched is not None else {}

    async def search_messages(
        self, query: str, since: datetime | None = None
    ) -> list[InboxMessage]:
        return self._search_results

    async def get_message(self, message_id: str) -> InboxMessage | None:
        return self._fetched.get(message_id)


def _deps(scanner: _ScriptedScanner) -> InterviewScanDependencies:
    return InterviewScanDependencies(
        inbox_scanner=scanner,
        detector=HeuristicInviteDetector(),
        prep_generator=HeuristicPrepPacketGenerator(),
        interview_repository=InMemoryInterviewRepository(),
        master_cv_repository=InMemoryMasterCvRepository(),
        application_repository=InMemoryApplicationRepository(),
        validation_log=InMemoryValidationRunLog(),
    )


# --- EXIT TEST: missing ID or mismatched quote cannot create a record -------------------


async def test_missing_message_id_cannot_create_a_record(settings: Settings) -> None:
    scanner = _ScriptedScanner([_message("")])
    deps = _deps(scanner)

    result = await run_interview_scan(deps, settings)

    assert result.detected == 1
    assert result.rejected == 1
    assert result.new_interviews == 0
    assert deps.interview_repository.list_interviews(settings.pipeline_user_id) == []
    (run,) = deps.validation_log.list_runs(settings.pipeline_user_id, KIND_INTERVIEW_VERIFICATION)
    assert run.passed is False
    assert any("gmail_message_id" in reason for reason in run.detail)


async def test_mismatched_quote_cannot_create_a_record(settings: Settings) -> None:
    # The provider's real body differs from what detection saw: verification must
    # re-check against the RE-FETCHED body and refuse.
    scanner = _ScriptedScanner(
        [_message("msg-1")],
        fetched={"msg-1": _message("msg-1", body="Totally different content.")},
    )
    deps = _deps(scanner)

    result = await run_interview_scan(deps, settings)

    assert result.rejected == 1
    assert deps.interview_repository.list_interviews(settings.pipeline_user_id) == []
    (run,) = deps.validation_log.list_runs(settings.pipeline_user_id, KIND_INTERVIEW_VERIFICATION)
    assert run.passed is False
    assert any("not a substring" in reason for reason in run.detail)


async def test_unfetchable_message_cannot_create_a_record(settings: Settings) -> None:
    scanner = _ScriptedScanner([_message("msg-gone")], fetched={})
    deps = _deps(scanner)

    result = await run_interview_scan(deps, settings)

    assert result.rejected == 1
    assert deps.interview_repository.list_interviews(settings.pipeline_user_id) == []


# --- EXIT TEST: a valid invite lands in the confirm queue only ---------------------------


async def test_valid_invite_lands_in_confirm_queue_only(settings: Settings) -> None:
    message = _message("msg-1")
    scanner = _ScriptedScanner([message], fetched={"msg-1": message})
    deps = _deps(scanner)

    result = await run_interview_scan(deps, settings)

    assert result.verified == 1
    assert result.new_interviews == 1
    assert result.packets_generated == 0

    (interview,) = deps.interview_repository.list_interviews(settings.pipeline_user_id)
    # Confirm queue only: detected, never directly confirmed/scheduled, no packet.
    assert interview.stage is InterviewStage.DETECTED
    assert deps.interview_repository.get_prep_packet(interview.id) is None
    assert interview.gmail_message_id == "msg-1"
    assert interview.evidence_quote in message.body  # verbatim provenance stored

    (run,) = deps.validation_log.list_runs(settings.pipeline_user_id, KIND_INTERVIEW_VERIFICATION)
    assert run.passed is True


# --- verification unit rules ---------------------------------------------------------------


def test_verify_rejects_wrong_message_identity() -> None:
    fetched = _message("msg-OTHER")
    failure = verify_invite_provenance("msg-1", _INVITE_BODY, fetched)
    assert failure is not None and "not the cited" in failure


def test_verify_requires_a_quote() -> None:
    fetched = _message("msg-1")
    failure = verify_invite_provenance("msg-1", "   ", fetched)
    assert failure is not None and "evidence_quote" in failure


def test_verify_passes_on_verbatim_quote() -> None:
    fetched = _message("msg-1")
    quote = "We'd like to schedule a 60-minute technical interview next week"
    assert verify_invite_provenance("msg-1", quote, fetched) is None


# --- validation-run log parity ---------------------------------------------------------------


@pytest.fixture(params=["in_memory", "sql"])
def run_log(request: pytest.FixtureRequest, tmp_path: Path) -> ValidationRunLog:
    if request.param == "in_memory":
        return InMemoryValidationRunLog()
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'runs.db'}")
    create_all(engine)
    return SqlValidationRunLog(create_session_factory(engine))


def test_validation_run_log_records_and_filters(run_log: ValidationRunLog) -> None:
    run_log.record("u1", KIND_INTERVIEW_VERIFICATION, "message:m1", passed=True)
    failed = run_log.record(
        "u1", "par_validation", "claim:7", passed=False, detail=("problem_missing: x",)
    )
    assert failed.detail == ("problem_missing: x",)
    assert len(run_log.list_runs("u1")) == 2
    assert [r.subject_ref for r in run_log.list_runs("u1", "par_validation")] == ["claim:7"]
    assert run_log.list_runs("someone-else") == []


# --- mode guard ---------------------------------------------------------------------------


def test_mode_guard_allows_mocks_in_dev_mode(settings: Settings) -> None:
    deps = _deps(_ScriptedScanner([]))
    assert_mode_guard(settings, None, deps)  # gmail_enabled=false -> fine


def test_mode_guard_refuses_mock_inbox_in_real_mode(settings: Settings) -> None:
    from app.integrations.mock.inbox import MockInboxScanner

    from tests.conftest import INBOX_FIXTURES_DIR

    real_mode = settings.model_copy(update={"gmail_enabled": True})
    deps = InterviewScanDependencies(
        inbox_scanner=MockInboxScanner(INBOX_FIXTURES_DIR),
        detector=HeuristicInviteDetector(),
        prep_generator=HeuristicPrepPacketGenerator(),
        interview_repository=InMemoryInterviewRepository(),
        master_cv_repository=InMemoryMasterCvRepository(),
        application_repository=InMemoryApplicationRepository(),
        validation_log=InMemoryValidationRunLog(),
    )
    with pytest.raises(ModeGuardError):
        assert_mode_guard(real_mode, None, deps)


def test_scheduler_main_enforces_the_mode_guard(settings: Settings) -> None:
    from app.scheduler import main

    real_mode = settings.model_copy(update={"gmail_enabled": True})
    deps = InterviewScanDependencies(
        inbox_scanner=_ScriptedScanner([]),  # type: ignore[arg-type]
        detector=HeuristicInviteDetector(),
        prep_generator=HeuristicPrepPacketGenerator(),
        interview_repository=InMemoryInterviewRepository(),
        master_cv_repository=InMemoryMasterCvRepository(),
        application_repository=InMemoryApplicationRepository(),
        validation_log=InMemoryValidationRunLog(),
    )
    # A scripted (non-Mock*) scanner passes; the guard targets the mock classes.
    assert main(
        ["--once", "--job", "interviews"], settings=real_mode, interview_dependencies=deps
    ) in (0, 1)

    from app.integrations.mock.inbox import MockInboxScanner

    from tests.conftest import INBOX_FIXTURES_DIR

    mock_deps = InterviewScanDependencies(
        inbox_scanner=MockInboxScanner(INBOX_FIXTURES_DIR),
        detector=HeuristicInviteDetector(),
        prep_generator=HeuristicPrepPacketGenerator(),
        interview_repository=InMemoryInterviewRepository(),
        master_cv_repository=InMemoryMasterCvRepository(),
        application_repository=InMemoryApplicationRepository(),
        validation_log=InMemoryValidationRunLog(),
    )
    with pytest.raises(ModeGuardError):
        main(
            ["--once", "--job", "interviews"], settings=real_mode, interview_dependencies=mock_deps
        )
