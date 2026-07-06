"""Claim repository contract tests, run against both implementations.

The load-bearing semantics: evidence/experience dedupe, and the idempotency rule —
re-extraction replaces only unreviewed claims, never a human decision, and never
re-queues content a human already decided on.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db.base import Base
from app.db.claim_repository import SqlClaimRepository
from app.db.session import create_session_factory
from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_USER_ATTESTATION,
    ClaimEvidenceRef,
    ClaimField,
    ClaimRepository,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    RejectionReasonRequiredError,
    ResultStatus,
    StorableClaim,
)
from app.services.claim_repository import InMemoryClaimRepository

RepoFactory = Callable[[], ClaimRepository]


def _sql_repo(tmp_path: Path) -> SqlClaimRepository:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'claims.db'}")
    Base.metadata.create_all(engine)
    return SqlClaimRepository(create_session_factory(engine))


@pytest.fixture(params=["in_memory", "sql"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> ClaimRepository:
    if request.param == "in_memory":
        return InMemoryClaimRepository()
    return _sql_repo(tmp_path)


_SEED = ExperienceSeed(name="carrier-etl", section=ExperienceSection.PROJECTS_HACKATHONS)
_CHUNK = EvidenceChunk(SOURCE_GITHUB_COMMIT, "c001", "Fixed dedup key mismatch with Kafka")


def _storable(action: str = "Fixed dedup key mismatch with Kafka") -> StorableClaim:
    return StorableClaim(
        draft=DraftClaim(
            action_text=action,
            action_tools=("Kafka",),
            evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
        ),
        status=ClaimStatus.PENDING_REVIEW,
        result_status=ResultStatus.UNVERIFIED,
        validation_flags=("problem_missing: no Problem declared",),
    )


# --- experiences ---------------------------------------------------------------------


def test_experience_dedupes_on_user_and_name(repo: ClaimRepository) -> None:
    first = repo.upsert_experience("u1", _SEED)
    again = repo.upsert_experience("u1", _SEED)
    other_user = repo.upsert_experience("u2", _SEED)

    assert first.id == again.id
    assert other_user.id != first.id
    assert first.section is ExperienceSection.PROJECTS_HACKATHONS


def test_existing_experience_keeps_user_assigned_fields(repo: ClaimRepository) -> None:
    first = repo.upsert_experience("u1", _SEED)
    changed_seed = ExperienceSeed(
        name="carrier-etl",
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        subtitle="new subtitle",
    )
    again = repo.upsert_experience("u1", changed_seed)
    # Re-extraction never overrides what review owns (section, order).
    assert again.section is first.section
    assert again.sort_order == first.sort_order


def test_sort_order_reflects_insertion_order(repo: ClaimRepository) -> None:
    a = repo.upsert_experience("u1", _SEED)
    b = repo.upsert_experience(
        "u1", ExperienceSeed(name="other", section=ExperienceSection.PROFESSIONAL_EXPERIENCE)
    )
    assert (a.sort_order, b.sort_order) == (0, 1)
    assert [e.id for e in repo.list_experiences("u1")] == [a.id, b.id]


# --- evidence --------------------------------------------------------------------------


def test_evidence_dedupes_and_updates_text(repo: ClaimRepository) -> None:
    first = repo.upsert_evidence("u1", _CHUNK)
    unchanged = repo.upsert_evidence("u1", _CHUNK)
    assert first.id == unchanged.id

    updated = repo.upsert_evidence(
        "u1", EvidenceChunk(_CHUNK.source_type, _CHUNK.source_ref, "amended message")
    )
    assert updated.id == first.id
    assert repo.get_evidence(first.id) is not None
    assert repo.get_evidence(first.id).chunk_text == "amended message"  # type: ignore[union-attr]


def test_get_evidence_by_ref_finds_the_row_by_natural_key(repo: ClaimRepository) -> None:
    stored = repo.upsert_evidence("u1", _CHUNK)
    found = repo.get_evidence_by_ref("u1", _CHUNK.source_type, _CHUNK.source_ref)
    assert found is not None and found.id == stored.id
    assert repo.get_evidence_by_ref("u1", _CHUNK.source_type, "no-such-ref") is None
    assert repo.get_evidence_by_ref("u2", _CHUNK.source_type, _CHUNK.source_ref) is None


def test_user_attestations_never_surface_in_the_unassigned_queue(repo: ClaimRepository) -> None:
    """Story/claim attestations are human answers, not source chunks awaiting roster
    assignment — they must not pollute the roster's unassigned-evidence review queue."""
    source = repo.upsert_evidence("u1", _CHUNK)  # a real unassigned source chunk
    assert [e.id for e in repo.list_unassigned_evidence("u1")] == [source.id]

    attestation = repo.upsert_evidence(
        "u1",
        EvidenceChunk(SOURCE_USER_ATTESTATION, "story:1:problem", "Analysts reconciled by hand"),
    )
    # The attestation is retrievable by ref but stays out of the unassigned queue.
    assert repo.get_evidence_by_ref("u1", SOURCE_USER_ATTESTATION, "story:1:problem") is not None
    assert attestation.experience_id is None
    assert [e.id for e in repo.list_unassigned_evidence("u1")] == [source.id]


# --- claims: idempotent replace ----------------------------------------------------------


def test_rerun_replaces_unreviewed_claims_without_duplicating(repo: ClaimRepository) -> None:
    experience = repo.upsert_experience("u1", _SEED)
    repo.replace_unreviewed_claims("u1", experience.id, [_storable()])
    repo.replace_unreviewed_claims("u1", experience.id, [_storable()])

    claims = repo.list_claims("u1")
    assert len(claims) == 1
    assert claims[0].status is ClaimStatus.PENDING_REVIEW
    assert claims[0].validation_flags == ("problem_missing: no Problem declared",)
    assert len(claims[0].evidence) == 1
    assert claims[0].evidence[0].field is ClaimField.ACTION


def test_rerun_never_touches_reviewed_claims_or_requeues_their_content(
    repo: ClaimRepository,
) -> None:
    experience = repo.upsert_experience("u1", _SEED)
    inserted = repo.replace_unreviewed_claims("u1", experience.id, [_storable()])
    approved = repo.transition_claim(inserted[0].id, ClaimStatus.APPROVED)

    again = repo.replace_unreviewed_claims(
        "u1", experience.id, [_storable(), _storable("Built the exporter in Python")]
    )

    # The approved claim survives; its content is not re-queued; new content is.
    assert [c.action_text for c in again] == ["Built the exporter in Python"]
    claims = repo.list_claims("u1")
    assert len(claims) == 2
    assert repo.get_claim(approved.id) is not None
    assert repo.get_claim(approved.id).status is ClaimStatus.APPROVED  # type: ignore[union-attr]


def test_only_approved_claims_come_back_from_the_approved_filter(
    repo: ClaimRepository,
) -> None:
    experience = repo.upsert_experience("u1", _SEED)
    inserted = repo.replace_unreviewed_claims(
        "u1", experience.id, [_storable(), _storable("Built the exporter in Python")]
    )
    repo.transition_claim(inserted[0].id, ClaimStatus.APPROVED)

    approved = repo.list_claims("u1", status=ClaimStatus.APPROVED)
    assert [c.id for c in approved] == [inserted[0].id]


# --- claims: transitions -----------------------------------------------------------------


def test_transition_enforces_the_state_machine(repo: ClaimRepository) -> None:
    experience = repo.upsert_experience("u1", _SEED)
    inserted = repo.replace_unreviewed_claims("u1", experience.id, [_storable()])

    rejected = repo.transition_claim(
        inserted[0].id, ClaimStatus.REJECTED, review_note="not my work"
    )
    assert rejected.status is ClaimStatus.REJECTED
    assert rejected.review_note == "not my work"
    assert rejected.reviewed_at is not None


def test_rejection_without_reason_is_refused(repo: ClaimRepository) -> None:
    experience = repo.upsert_experience("u1", _SEED)
    inserted = repo.replace_unreviewed_claims("u1", experience.id, [_storable()])
    with pytest.raises(RejectionReasonRequiredError):
        repo.transition_claim(inserted[0].id, ClaimStatus.REJECTED)
    assert repo.get_claim(inserted[0].id).status is ClaimStatus.PENDING_REVIEW  # type: ignore[union-attr]


def test_transition_unknown_claim_raises_lookup_error(repo: ClaimRepository) -> None:
    with pytest.raises(LookupError):
        repo.transition_claim(999, ClaimStatus.APPROVED)


def test_apply_claim_edit_persists_updates_links_and_attestation(
    repo: ClaimRepository,
) -> None:
    from app.domain.claims import (
        SOURCE_USER_ATTESTATION,
        ClaimEdits,
        ClaimField,
        ResultKind,
        ResultStatus,
        plan_claim_edit,
    )

    experience = repo.upsert_experience("u1", _SEED)
    inserted = repo.replace_unreviewed_claims("u1", experience.id, [_storable()])
    claim = inserted[0]

    plan = plan_claim_edit(
        claim,
        ClaimEdits(
            result_text="Cut ingest failures from 12% to 0.5%",
            result_kind=ResultKind.QUANTIFIED,
            result_metric_json={"resolves": "quality"},
        ),
    )
    updated = repo.apply_claim_edit("u1", plan)

    assert updated.result_text == "Cut ingest failures from 12% to 0.5%"
    assert updated.result_kind is ResultKind.QUANTIFIED
    assert updated.result_status is ResultStatus.USER_ATTESTED
    assert updated.result_metric_json == {"resolves": "quality"}
    assert updated.validation_flags == ()  # human decision supersedes the validator

    result_links = [ln for ln in updated.evidence if ln.field is ClaimField.RESULT]
    assert len(result_links) == 1
    evidence = repo.get_evidence(result_links[0].evidence_id)
    assert evidence is not None
    assert evidence.source_type == SOURCE_USER_ATTESTATION
    assert evidence.chunk_text == "Cut ingest failures from 12% to 0.5%"

    # A re-edit updates the same attestation row instead of accreting links.
    replan = plan_claim_edit(
        repo.get_claim(claim.id),  # type: ignore[arg-type]
        ClaimEdits(
            result_text="Cut ingest failures from 12% to 0.4%",
            result_kind=ResultKind.QUANTIFIED,
        ),
    )
    reedited = repo.apply_claim_edit("u1", replan)
    result_links = [ln for ln in reedited.evidence if ln.field is ClaimField.RESULT]
    assert len(result_links) == 1
    assert result_links[0].outcome_quote == "Cut ingest failures from 12% to 0.4%"


def test_update_experience_layout_persists_section_and_order(repo: ClaimRepository) -> None:
    from app.domain.claims import ExperienceSection

    experience = repo.upsert_experience("u1", _SEED)
    updated = repo.update_experience_layout(
        experience.id, section=ExperienceSection.PROFESSIONAL_EXPERIENCE, sort_order=7
    )
    assert updated.section is ExperienceSection.PROFESSIONAL_EXPERIENCE
    assert updated.sort_order == 7
    stored = repo.list_experiences("u1")[0]
    assert stored.section is ExperienceSection.PROFESSIONAL_EXPERIENCE
    assert stored.sort_order == 7
    with pytest.raises(LookupError):
        repo.update_experience_layout(999, sort_order=1)


def test_rejected_claims_are_retained_not_deleted(repo: ClaimRepository) -> None:
    experience = repo.upsert_experience("u1", _SEED)
    inserted = repo.replace_unreviewed_claims("u1", experience.id, [_storable()])
    repo.transition_claim(inserted[0].id, ClaimStatus.REJECTED, review_note="duplicate")

    repo.replace_unreviewed_claims("u1", experience.id, [_storable()])
    claims = repo.list_claims("u1")
    # The rejected row is still there and the matching content was not re-queued.
    assert len(claims) == 1
    assert claims[0].status is ClaimStatus.REJECTED
