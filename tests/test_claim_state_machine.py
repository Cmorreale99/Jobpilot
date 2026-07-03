"""Claim state machine: extracted -> pending_review -> approved | rejected(reason)."""

from __future__ import annotations

import pytest
from app.domain.applications import InvalidTransitionError
from app.domain.claims import (
    ClaimStatus,
    RejectionReasonRequiredError,
    validate_claim_transition,
)


def test_extracted_promotes_to_pending_review() -> None:
    validate_claim_transition(ClaimStatus.EXTRACTED, ClaimStatus.PENDING_REVIEW)


def test_pending_review_can_be_approved_or_rejected() -> None:
    validate_claim_transition(ClaimStatus.PENDING_REVIEW, ClaimStatus.APPROVED)
    validate_claim_transition(
        ClaimStatus.PENDING_REVIEW, ClaimStatus.REJECTED, review_note="inflated"
    )


def test_rejection_requires_a_reason() -> None:
    with pytest.raises(RejectionReasonRequiredError):
        validate_claim_transition(ClaimStatus.PENDING_REVIEW, ClaimStatus.REJECTED)
    with pytest.raises(RejectionReasonRequiredError):
        validate_claim_transition(
            ClaimStatus.PENDING_REVIEW, ClaimStatus.REJECTED, review_note="   "
        )


def test_extracted_cannot_skip_review() -> None:
    with pytest.raises(InvalidTransitionError):
        validate_claim_transition(ClaimStatus.EXTRACTED, ClaimStatus.APPROVED)
    with pytest.raises(InvalidTransitionError):
        validate_claim_transition(ClaimStatus.EXTRACTED, ClaimStatus.REJECTED, review_note="x")


@pytest.mark.parametrize("terminal", [ClaimStatus.APPROVED, ClaimStatus.REJECTED])
def test_review_decisions_are_terminal(terminal: ClaimStatus) -> None:
    for new_status in ClaimStatus:
        with pytest.raises(InvalidTransitionError):
            validate_claim_transition(terminal, new_status, review_note="reason")
