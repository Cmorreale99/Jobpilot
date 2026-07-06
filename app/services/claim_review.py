"""The claim review layer: approve, edit-then-approve, reject, and the queues.

Human-in-the-loop core of V2. Three actions, mirroring the outline's review card:

* **approve** — the claim becomes canonical as-is. Refused for claims carrying
  ``validation_flags``: a flagged claim must be edit-attested (the human supersedes
  the validator, with provenance) or rejected — never waved through unchanged.
* **edit + approve** — the reviewer changes fields first; every edited field group
  gets ``user_attestation`` provenance (an evidence row holding the attested text,
  linked to the claim), so traceability survives the edit. An edited/supplied Result
  becomes ``result_status=user_attested`` — this is also how the Missing-Results
  queue resolves with a number.
* **reject** — requires a reason; rejected claims are retained, never deleted
  (approve/reject/edit decisions are the V3 golden-set labels).

The Missing-Results queue is deliberately just a filtered view of the same layer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.claims import (
    Claim,
    ClaimEdits,
    ClaimRepository,
    ClaimStatus,
    ResultKind,
    plan_claim_edit,
)


def review_queue(
    repository: ClaimRepository, user_id: str, *, missing_results_only: bool = False
) -> list[Claim]:
    """Claims awaiting a human decision; optionally only those missing a Result."""
    pending = repository.list_claims(user_id, status=ClaimStatus.PENDING_REVIEW)
    if missing_results_only:
        return [c for c in pending if c.result_kind is ResultKind.MISSING]
    return pending


class FlaggedClaimApprovalError(Exception):
    """Approve-as-is was attempted on a claim with validation flags.

    Persisted flags are INTEGRITY violations only (ungrounded tools, coupling,
    duplicate outcome spans): absence codes never persist as flags — a missing
    Problem is readiness data, not a claim defect (V3 Phase 0 flag split) — so
    this gate never fires on honest absence.
    """


def approve_claim(repository: ClaimRepository, claim_id: int) -> Claim:
    """Approve as-is (``pending_review -> approved``); refused for integrity flags."""
    claim = repository.get_claim(claim_id)
    if claim is None:
        raise LookupError(f"no claim with id {claim_id}")
    if claim.validation_flags:
        raise FlaggedClaimApprovalError(
            f"claim {claim_id} carries {len(claim.validation_flags)} validation flag(s) "
            "and cannot be approved as-is - edit-attest it (which records your "
            "attestation and clears the flags) or reject it: " + "; ".join(claim.validation_flags)
        )
    return repository.transition_claim(claim_id, ClaimStatus.APPROVED)


def reject_claim(repository: ClaimRepository, claim_id: int, reason: str) -> Claim:
    """Reject with a required reason; the claim is kept as labeled data."""
    return repository.transition_claim(claim_id, ClaimStatus.REJECTED, review_note=reason)


def edit_and_approve_claim(
    repository: ClaimRepository,
    claim_id: int,
    edits: ClaimEdits,
    *,
    now: datetime | None = None,
) -> Claim:
    """Apply the reviewer's edits with attestation provenance, then approve.

    The edit plan is computed purely in the domain (``plan_claim_edit``) and refused
    outright for non-pending claims, so an illegal edit never half-applies.
    """
    claim = repository.get_claim(claim_id)
    if claim is None:
        raise LookupError(f"no claim with id {claim_id}")
    plan = plan_claim_edit(claim, edits)
    repository.apply_claim_edit(claim.user_id, plan)
    return repository.transition_claim(
        claim_id, ClaimStatus.APPROVED, reviewed_at=now or datetime.now(tz=UTC)
    )
