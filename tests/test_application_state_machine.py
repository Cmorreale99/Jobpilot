"""Application + outreach state machines: every legal path, illegal jumps rejected."""

from __future__ import annotations

import pytest
from app.domain.applications import (
    APPLICATION_TRANSITIONS,
    OUTREACH_TRANSITIONS,
    ApplicationStatus,
    InvalidTransitionError,
    OutreachStatus,
    validate_transition,
)


def test_every_application_status_has_transition_rules() -> None:
    assert set(APPLICATION_TRANSITIONS) == set(ApplicationStatus)


def test_every_outreach_status_has_transition_rules() -> None:
    assert set(OUTREACH_TRANSITIONS) == set(OutreachStatus)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (ApplicationStatus.DRAFTED, ApplicationStatus.APPLIED),
        (ApplicationStatus.DRAFTED, ApplicationStatus.IGNORED),
        (ApplicationStatus.APPLIED, ApplicationStatus.INTERVIEWING),
        (ApplicationStatus.APPLIED, ApplicationStatus.REJECTED),
        (ApplicationStatus.APPLIED, ApplicationStatus.OFFER),
        (ApplicationStatus.INTERVIEWING, ApplicationStatus.REJECTED),
        (ApplicationStatus.INTERVIEWING, ApplicationStatus.OFFER),
    ],
)
def test_legal_application_transitions(current: ApplicationStatus, new: ApplicationStatus) -> None:
    validate_transition(APPLICATION_TRANSITIONS, current, new)  # must not raise


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (ApplicationStatus.DRAFTED, ApplicationStatus.INTERVIEWING),  # must apply first
        (ApplicationStatus.DRAFTED, ApplicationStatus.OFFER),
        (ApplicationStatus.APPLIED, ApplicationStatus.DRAFTED),  # no going back
        (ApplicationStatus.REJECTED, ApplicationStatus.APPLIED),  # terminal
        (ApplicationStatus.OFFER, ApplicationStatus.INTERVIEWING),  # terminal
        (ApplicationStatus.IGNORED, ApplicationStatus.APPLIED),  # terminal
        (ApplicationStatus.DRAFTED, ApplicationStatus.DRAFTED),  # self-loop is not a transition
    ],
)
def test_illegal_application_transitions_raise(
    current: ApplicationStatus, new: ApplicationStatus
) -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition(APPLICATION_TRANSITIONS, current, new)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (OutreachStatus.DRAFTED, OutreachStatus.APPROVED),
        (OutreachStatus.DRAFTED, OutreachStatus.DISCARDED),
        (OutreachStatus.APPROVED, OutreachStatus.SENT),
    ],
)
def test_legal_outreach_transitions(current: OutreachStatus, new: OutreachStatus) -> None:
    validate_transition(OUTREACH_TRANSITIONS, current, new)  # must not raise


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (OutreachStatus.DRAFTED, OutreachStatus.SENT),  # nothing sends without approval
        (OutreachStatus.APPROVED, OutreachStatus.DRAFTED),
        (OutreachStatus.APPROVED, OutreachStatus.DISCARDED),
        (OutreachStatus.SENT, OutreachStatus.APPROVED),  # terminal
        (OutreachStatus.DISCARDED, OutreachStatus.APPROVED),  # terminal — no resurrection
    ],
)
def test_illegal_outreach_transitions_raise(current: OutreachStatus, new: OutreachStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition(OUTREACH_TRANSITIONS, current, new)
