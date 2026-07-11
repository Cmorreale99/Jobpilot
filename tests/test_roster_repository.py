"""Roster persistence semantics, against both repository implementations.

The rules that make the roster trustworthy: proposals never override a human decision
(no downgrade, no resurrection of discarded entities), renames keep the old name as an
alias, merges move claims + evidence assignments and are terminal, and the lifecycle
is a validated state machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db.base import Base
from app.db.claim_repository import SqlClaimRepository
from app.db.session import create_session_factory
from app.domain.applications import InvalidTransitionError
from app.domain.claims import (
    ASSIGNMENT_HUMAN,
    SOURCE_DRIVE,
    ClaimEvidenceRef,
    ClaimField,
    ClaimRepository,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    ResultStatus,
    StorableClaim,
)
from app.services.claim_repository import InMemoryClaimRepository


@pytest.fixture(params=["in_memory", "sql"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> ClaimRepository:
    if request.param == "in_memory":
        return InMemoryClaimRepository()
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'roster.db'}")
    Base.metadata.create_all(engine)
    return SqlClaimRepository(create_session_factory(engine))


def _seed(name: str, aliases: tuple[str, ...] = ()) -> ExperienceSeed:
    return ExperienceSeed(
        name=name,
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        kind=ExperienceKind.EMPLOYER_ROLE,
        aliases=aliases,
    )


def test_proposals_land_proposed_and_dedupe_on_name_and_alias(repo: ClaimRepository) -> None:
    first = repo.propose_experience("u1", _seed("Wellington — Tech Intern", ("Wellington",)))
    assert first.status is ExperienceStatus.PROPOSED

    # Same name, an alias of the existing row, and a seed whose alias matches the
    # existing name all resolve to the SAME row — no duplicates, ever.
    assert repo.propose_experience("u1", _seed("Wellington — Tech Intern")).id == first.id
    assert repo.propose_experience("u1", _seed("wellington")).id == first.id
    alias_seed = _seed("W. Mgmt", ("Wellington — Tech Intern",))
    assert repo.propose_experience("u1", alias_seed).id == first.id


def test_proposal_never_downgrades_confirmed_or_resurrects_discarded(
    repo: ClaimRepository,
) -> None:
    confirmed = repo.propose_experience("u1", _seed("Cooper.ai — Data Engineer"))
    repo.set_experience_status(confirmed.id, ExperienceStatus.CONFIRMED)
    re_proposed = repo.propose_experience("u1", _seed("Cooper.ai — Data Engineer"))
    assert re_proposed.id == confirmed.id
    assert re_proposed.status is ExperienceStatus.CONFIRMED  # untouched

    junk = repo.propose_experience("u1", _seed("Degree Completion Form.pdf"))
    repo.set_experience_status(junk.id, ExperienceStatus.DISCARDED)
    again = repo.propose_experience("u1", _seed("Degree Completion Form.pdf"))
    assert again.id == junk.id
    assert again.status is ExperienceStatus.DISCARDED  # junk stays discarded


def test_lifecycle_is_a_validated_state_machine(repo: ClaimRepository) -> None:
    entity = repo.propose_experience("u1", _seed("Wellington"))
    confirmed = repo.set_experience_status(entity.id, ExperienceStatus.CONFIRMED)
    assert confirmed.status is ExperienceStatus.CONFIRMED
    with pytest.raises(InvalidTransitionError):
        repo.set_experience_status(entity.id, ExperienceStatus.PROPOSED)  # no downgrade
    discarded = repo.set_experience_status(entity.id, ExperienceStatus.DISCARDED)
    with pytest.raises(InvalidTransitionError):
        repo.set_experience_status(discarded.id, ExperienceStatus.CONFIRMED)  # terminal


def test_rename_keeps_the_old_name_as_an_alias(repo: ClaimRepository) -> None:
    entity = repo.propose_experience("u1", _seed("resume_final (1).pdf"))
    renamed = repo.update_experience_details(
        entity.id, name="Coopersmith Data — Data Engineer", dates="Jun 2025–Present"
    )
    assert renamed.name == "Coopersmith Data — Data Engineer"
    assert "resume_final (1).pdf" in renamed.aliases
    assert renamed.dates == "Jun 2025–Present"
    # Future detections of the old filename resolve to the renamed entity.
    assert repo.propose_experience("u1", _seed("resume_final (1).pdf")).id == entity.id


def test_merge_moves_claims_and_evidence_and_is_terminal(repo: ClaimRepository) -> None:
    target = repo.propose_experience("u1", _seed("Coopersmith Data — Data Engineer"))
    repo.set_experience_status(target.id, ExperienceStatus.CONFIRMED)
    source = repo.propose_experience("u1", _seed("resume_final_FINAL.pdf"))

    chunk = EvidenceChunk(SOURCE_DRIVE, "drv_v2#chars=0-80", "Rebuilt the exporter with Python.")
    stored = repo.upsert_evidence("u1", chunk)
    repo.assign_evidence(stored.id, source.id)
    (claim,) = repo.replace_unreviewed_claims(
        "u1",
        source.id,
        [
            StorableClaim(
                draft=DraftClaim(
                    action_text="Rebuilt the settlement exporter with Python",
                    action_tools=("Python",),
                    evidence=(ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
            )
        ],
    )

    merged_target = repo.merge_experiences(source.id, target.id)

    moved_claim = repo.get_claim(claim.id)
    assert moved_claim is not None and moved_claim.experience_id == target.id
    assert [e.id for e in repo.list_assigned_evidence("u1", target.id)] == [stored.id]
    assert "resume_final_FINAL.pdf" in merged_target.aliases
    source_after = next(e for e in repo.list_experiences("u1") if e.id == source.id)
    assert source_after.status is ExperienceStatus.MERGED
    assert source_after.merged_into_id == target.id
    with pytest.raises(ValueError, match="cannot be merged"):
        repo.merge_experiences(source.id, target.id)  # terminal


def test_merge_target_must_be_confirmed(repo: ClaimRepository) -> None:
    a = repo.propose_experience("u1", _seed("A"))
    b = repo.propose_experience("u1", _seed("B"))
    with pytest.raises(ValueError, match="confirmed"):
        repo.merge_experiences(a.id, b.id)


def test_assign_evidence_sets_and_clears(repo: ClaimRepository) -> None:
    entity = repo.propose_experience("u1", _seed("Wellington"))
    repo.set_experience_status(entity.id, ExperienceStatus.CONFIRMED)
    stored = repo.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_DRIVE, "drv1#chars=0-40", "Wellington platform work.")
    )
    assert repo.assign_evidence(stored.id, entity.id).experience_id == entity.id
    assert [e.id for e in repo.list_assigned_evidence("u1", entity.id)] == [stored.id]
    assert repo.assign_evidence(stored.id, None).experience_id is None
    assert repo.list_assigned_evidence("u1", entity.id) == []


def test_assignment_method_stamped_and_merge_preserves_it(repo: ClaimRepository) -> None:
    """H1: the method label rides the assignment, and a roster merge — which moves
    evidence to the target — keeps the human's label intact."""
    source = repo.propose_experience("u1", _seed("Coopersmith — Data Engineer"))
    target = repo.propose_experience("u1", _seed("Coopersmith Data"))
    repo.set_experience_status(source.id, ExperienceStatus.CONFIRMED)
    repo.set_experience_status(target.id, ExperienceStatus.CONFIRMED)
    stored = repo.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_DRIVE, "drv1#chars=0-30", "Built the carrier ETL.")
    )
    assert stored.assignment_method is None  # unlabeled until assigned

    pinned = repo.assign_evidence(stored.id, source.id, method=ASSIGNMENT_HUMAN)
    assert pinned.assignment_method == ASSIGNMENT_HUMAN
    assert pinned.assigned_at is not None

    repo.merge_experiences(source.id, target.id)
    moved = repo.get_evidence(stored.id)
    assert moved is not None
    assert moved.experience_id == target.id
    assert moved.assignment_method == ASSIGNMENT_HUMAN  # the decision label survives


def test_unassigned_evidence_is_a_queryable_queue(repo: ClaimRepository) -> None:
    """V3 Phase 1 (red-team 3): unclaimed chunks are listable, per user, in id order."""
    entity = repo.propose_experience("u1", _seed("Wellington"))
    repo.set_experience_status(entity.id, ExperienceStatus.CONFIRMED)
    claimed = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv1", "assigned"))
    repo.assign_evidence(claimed.id, entity.id)
    orphan_a = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv2", "orphan a"))
    orphan_b = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv3", "orphan b"))
    repo.upsert_evidence("u2", EvidenceChunk(SOURCE_DRIVE, "drv4", "someone else's"))

    assert [e.id for e in repo.list_unassigned_evidence("u1")] == [orphan_a.id, orphan_b.id]
    repo.assign_evidence(orphan_a.id, entity.id)
    assert [e.id for e in repo.list_unassigned_evidence("u1")] == [orphan_b.id]
