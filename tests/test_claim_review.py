"""Review-layer service tests: approve, edit-then-approve with attestation, reject.

M9 exit test lives here: an edited field carries ``user_attestation`` provenance —
the attested text is stored as an evidence row and linked to the claim on that field.
"""

from __future__ import annotations

import pytest
from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_USER_ATTESTATION,
    ClaimEditError,
    ClaimEdits,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    CostDimension,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    Inefficiency,
    RejectionReasonRequiredError,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.services.claim_repository import InMemoryClaimRepository
from app.services.claim_review import (
    approve_claim,
    edit_and_approve_claim,
    reject_claim,
    review_queue,
)

_CHUNK = EvidenceChunk(SOURCE_GITHUB_COMMIT, "c001", "Fixed dedup key mismatch with Kafka")


def _seed_claim(
    repo: InMemoryClaimRepository,
    *,
    action: str = "Fixed dedup key mismatch with Kafka",
    result_kind: ResultKind = ResultKind.MISSING,
    flags: tuple[str, ...] = (),
) -> int:
    experience = repo.upsert_experience(
        "u1", ExperienceSeed(name="carrier-etl", section=ExperienceSection.PROJECTS_HACKATHONS)
    )
    inserted = repo.replace_unreviewed_claims(
        "u1",
        experience.id,
        [
            StorableClaim(
                draft=DraftClaim(
                    action_text=action,
                    action_tools=("Kafka",),
                    problem_text="Manual reconciliation took 10 hours per week",
                    problem_cost_dimension=CostDimension.TIME,
                    problem_inefficiency=Inefficiency.MANUAL,
                    result_kind=result_kind,
                    evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
                validation_flags=flags,
            )
        ],
    )
    return inserted[0].id


def _attestation_links(repo: InMemoryClaimRepository, claim_id: int, field: ClaimField) -> list:
    claim = repo.get_claim(claim_id)
    assert claim is not None
    links = []
    for link in claim.evidence:
        evidence = repo.get_evidence(link.evidence_id)
        assert evidence is not None
        if link.field is field and evidence.source_type == SOURCE_USER_ATTESTATION:
            links.append((link, evidence))
    return links


# --- approve / reject --------------------------------------------------------------


def test_approve_makes_the_claim_canonical() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo)
    approved = approve_claim(repo, claim_id)
    assert approved.status is ClaimStatus.APPROVED
    assert review_queue(repo, "u1") == []


def test_flagged_claim_cannot_be_approved_as_is() -> None:
    from app.services.claim_review import FlaggedClaimApprovalError

    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo, flags=("result_problem_coupling: result does not address …",))
    with pytest.raises(FlaggedClaimApprovalError):
        approve_claim(repo, claim_id)
    claim = repo.get_claim(claim_id)
    assert claim is not None and claim.status is ClaimStatus.PENDING_REVIEW  # untouched
    # test_edit_clears_validator_flags_human_supersedes covers the resolution path.


def test_reject_requires_a_reason_and_retains_the_claim() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo)
    with pytest.raises(RejectionReasonRequiredError):
        reject_claim(repo, claim_id, "   ")
    rejected = reject_claim(repo, claim_id, "inflated")
    assert rejected.status is ClaimStatus.REJECTED
    assert rejected.review_note == "inflated"
    assert repo.get_claim(claim_id) is not None  # retained, never deleted


# --- EXIT TEST: edited field carries user_attestation provenance ----------------------


def test_edited_action_carries_user_attestation_provenance() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo)

    updated = edit_and_approve_claim(
        repo,
        claim_id,
        ClaimEdits(action_text="Resolved the dedup key mismatch across carriers with Kafka"),
    )

    assert updated.status is ClaimStatus.APPROVED
    assert updated.action_text == "Resolved the dedup key mismatch across carriers with Kafka"

    attestations = _attestation_links(repo, claim_id, ClaimField.ACTION)
    assert len(attestations) == 1
    link, evidence = attestations[0]
    # The attested text IS the evidence, so traceability survives the edit.
    assert evidence.chunk_text == "Resolved the dedup key mismatch across carriers with Kafka"
    assert evidence.source_ref == f"claim:{claim_id}:action"
    # The original source evidence link is kept alongside the attestation.
    original = [
        (ln, ev)
        for ln in (repo.get_claim(claim_id) or updated).evidence
        if (ev := repo.get_evidence(ln.evidence_id)) is not None
        and ev.source_type == SOURCE_GITHUB_COMMIT
    ]
    assert original


def test_supplying_a_result_resolves_missing_results_as_user_attested() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo, result_kind=ResultKind.MISSING)
    assert review_queue(repo, "u1", missing_results_only=True)  # it is in the queue

    updated = edit_and_approve_claim(
        repo,
        claim_id,
        ClaimEdits(
            result_text="Cut weekly reconciliation effort from 10 hours to 1 hour",
            result_kind=ResultKind.QUANTIFIED,
            result_metric_json={"resolves": "time"},
        ),
    )

    assert updated.result_status is ResultStatus.USER_ATTESTED
    assert updated.result_kind is ResultKind.QUANTIFIED
    attestations = _attestation_links(repo, claim_id, ClaimField.RESULT)
    assert len(attestations) == 1
    link, evidence = attestations[0]
    assert link.outcome_quote == "Cut weekly reconciliation effort from 10 hours to 1 hour"
    assert evidence.chunk_text == "Cut weekly reconciliation effort from 10 hours to 1 hour"
    assert review_queue(repo, "u1", missing_results_only=True) == []


def test_edit_clears_validator_flags_human_supersedes() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo, flags=("problem_missing: whatever",))
    updated = edit_and_approve_claim(
        repo, claim_id, ClaimEdits(problem_text="Manual triage was eating 10 hours per week")
    )
    assert updated.validation_flags == ()


# --- edit guardrails -----------------------------------------------------------------


def test_edits_are_refused_on_reviewed_claims() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo)
    approve_claim(repo, claim_id)
    with pytest.raises(ClaimEditError):
        edit_and_approve_claim(repo, claim_id, ClaimEdits(action_text="rewritten"))


def test_empty_edit_is_refused() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo)
    with pytest.raises(ClaimEditError):
        edit_and_approve_claim(repo, claim_id, ClaimEdits())


def test_result_text_requires_explicit_kind() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo)
    with pytest.raises(ClaimEditError):
        edit_and_approve_claim(repo, claim_id, ClaimEdits(result_text="Cut effort by 90%"))


def test_edit_cannot_declare_a_result_missing() -> None:
    repo = InMemoryClaimRepository()
    claim_id = _seed_claim(repo)
    with pytest.raises(ClaimEditError):
        edit_and_approve_claim(
            repo, claim_id, ClaimEdits(result_text="x", result_kind=ResultKind.MISSING)
        )


# --- the missing-results queue is a filtered view --------------------------------------


def test_missing_results_queue_filters_the_same_layer() -> None:
    repo = InMemoryClaimRepository()
    _seed_claim(repo, action="Built the exporter in Python", result_kind=ResultKind.MISSING)
    full_queue = review_queue(repo, "u1")
    missing_queue = review_queue(repo, "u1", missing_results_only=True)
    assert {c.id for c in missing_queue} <= {c.id for c in full_queue}
    assert all(c.result_kind is ResultKind.MISSING for c in missing_queue)
