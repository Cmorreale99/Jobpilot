"""Interview domain: invite detection, stage machine, heuristic prep packets."""

from __future__ import annotations

import pytest
from app.domain.applications import Application, ApplicationStatus, InvalidTransitionError
from app.domain.cv import MasterCv, ParClaim
from app.domain.interviews import (
    INTERVIEW_TRANSITIONS,
    HeuristicInviteDetector,
    HeuristicPrepPacketGenerator,
    InboxMessage,
    Interview,
    InterviewStage,
    normalize_company,
    validate_interview_transition,
)
from app.domain.tailoring import TailoredMaterials

_DETECTOR = HeuristicInviteDetector()


def _message(
    subject: str, body: str, sender: str = "recruiting@ledgerline.example"
) -> InboxMessage:
    return InboxMessage(message_id="m1", subject=subject, sender=sender, body=body)


# --- detection --------------------------------------------------------------------------


def test_detects_explicit_invite_with_company_and_title() -> None:
    invite = _DETECTOR.detect(
        _message(
            "Next steps",
            "Following up on your application for Staff Backend Engineer, Payments at "
            "Ledgerline. We'd love to set up a phone screen.",
        )
    )
    assert invite is not None
    assert invite.company == "Ledgerline"
    assert invite.job_title == "Staff Backend Engineer, Payments"


def test_ignores_rejections_even_with_invite_words() -> None:
    invite = _DETECTOR.detect(
        _message(
            "Your interview application",
            "Unfortunately we decided to move forward with other candidates.",
        )
    )
    assert invite is None


def test_ignores_unrelated_mail() -> None:
    assert _DETECTOR.detect(_message("Weekly digest", "25 new roles this week.")) is None


def test_title_absent_rather_than_invented() -> None:
    invite = _DETECTOR.detect(_message("Interview invitation", "Please pick a slot."))
    assert invite is not None
    assert invite.job_title is None  # nothing extractable -> nothing fabricated


def test_normalize_company_matches_across_spellings() -> None:
    assert normalize_company("Sentinel Pay") == normalize_company("sentinelpay")


# --- stage machine ----------------------------------------------------------------------


def test_every_stage_has_transition_rules() -> None:
    assert set(INTERVIEW_TRANSITIONS) == set(InterviewStage)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (InterviewStage.DETECTED, InterviewStage.SCHEDULED),
        (InterviewStage.DETECTED, InterviewStage.CANCELLED),
        (InterviewStage.SCHEDULED, InterviewStage.COMPLETED),
        (InterviewStage.SCHEDULED, InterviewStage.CANCELLED),
    ],
)
def test_legal_transitions(current: InterviewStage, new: InterviewStage) -> None:
    validate_interview_transition(current, new)  # must not raise


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (InterviewStage.DETECTED, InterviewStage.COMPLETED),  # must schedule first
        (InterviewStage.COMPLETED, InterviewStage.SCHEDULED),  # terminal
        (InterviewStage.CANCELLED, InterviewStage.SCHEDULED),  # terminal — no reopening
    ],
)
def test_illegal_transitions_raise(current: InterviewStage, new: InterviewStage) -> None:
    with pytest.raises(InvalidTransitionError):
        validate_interview_transition(current, new)


# --- heuristic prep packet ---------------------------------------------------------------


def _interview() -> Interview:
    return Interview(
        id=1,
        user_id="u1",
        source_message_id="m1",
        company="Ledgerline",
        job_title="Staff Backend Engineer",
        stage=InterviewStage.DETECTED,
    )


def _application() -> Application:
    return Application(
        id=1,
        user_id="u1",
        job_source="mock",
        job_external_id="j1",
        job_title="Staff Backend Engineer",
        job_company="Ledgerline",
        master_cv_version=1,
        status=ApplicationStatus.APPLIED,
        materials=TailoredMaterials(
            summary="Fit for Staff Backend Engineer at Ledgerline.",
            highlights=("Re-architected the settlement pipeline — cut runtime by 70%",),
            cover_letter="(letter)",
        ),
    )


def _cv() -> MasterCv:
    return MasterCv(
        claims=[
            ParClaim(
                action="Introduced distributed tracing across services",
                source_ref="resume",
            )
        ]
    )


def test_packet_grounds_in_application_materials() -> None:
    packet = HeuristicPrepPacketGenerator().generate(_interview(), _cv(), _application())
    assert packet.interview_id == 1
    assert "Staff Backend Engineer at Ledgerline" in packet.content
    assert "Re-architected the settlement pipeline" in packet.content
    assert "Questions to ask" in packet.content


def test_packet_without_application_uses_cv_claims() -> None:
    packet = HeuristicPrepPacketGenerator().generate(_interview(), _cv(), None)
    assert "Introduced distributed tracing across services" in packet.content


def test_packet_is_deterministic() -> None:
    generator = HeuristicPrepPacketGenerator()
    first = generator.generate(_interview(), _cv(), _application())
    second = generator.generate(_interview(), _cv(), _application())
    assert first == second
