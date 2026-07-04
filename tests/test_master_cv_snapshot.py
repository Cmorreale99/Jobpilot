"""Master CV snapshot tests, against both stores.

Two M9 exit tests live here:
* an unapproved claim cannot appear in ANY Master CV version (enforced at the
  version-snapshot query, defended again in the builder);
* section reassignment changes which render group an experience lands in.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db.base import Base
from app.db.master_cv_snapshot_store import SqlMasterCvSnapshotStore
from app.db.session import create_session_factory
from app.domain.claims import (
    SOURCE_DRIVE,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    ResultStatus,
    StorableClaim,
)
from app.domain.master_cv_snapshot import MasterCvSnapshotStore, build_snapshot_content
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore, create_master_cv_snapshot

StoreFactory = Callable[[], MasterCvSnapshotStore]


@pytest.fixture(params=["in_memory", "sql"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> MasterCvSnapshotStore:
    if request.param == "in_memory":
        return InMemorySnapshotStore()
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'snapshots.db'}")
    Base.metadata.create_all(engine)
    return SqlMasterCvSnapshotStore(create_session_factory(engine))


_CHUNK = EvidenceChunk(SOURCE_DRIVE, "drv1", "Automated triage with Python.")


def _storable(action: str) -> StorableClaim:
    return StorableClaim(
        draft=DraftClaim(
            action_text=action,
            action_tools=("Python",),
            evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
        ),
        status=ClaimStatus.PENDING_REVIEW,
        result_status=ResultStatus.UNVERIFIED,
    )


def _seed(
    repo: InMemoryClaimRepository,
    experience_name: str = "Wellington",
    section: ExperienceSection = ExperienceSection.PROFESSIONAL_EXPERIENCE,
    actions: tuple[str, ...] = ("Automated triage with Python",),
) -> tuple[int, list[int]]:
    experience = repo.upsert_experience("u1", ExperienceSeed(name=experience_name, section=section))
    inserted = repo.replace_unreviewed_claims("u1", experience.id, [_storable(a) for a in actions])
    return experience.id, [c.id for c in inserted]


# --- EXIT TEST: approved claims only, enforced at the snapshot query --------------------


def test_unapproved_claim_cannot_appear_in_any_version(store: MasterCvSnapshotStore) -> None:
    repo = InMemoryClaimRepository()
    _, claim_ids = _seed(
        repo,
        actions=(
            "Automated triage with Python",
            "Rewrote the settlement importer in Python",
        ),
    )
    approved_id, pending_id = claim_ids[0], claim_ids[1]
    repo.transition_claim(approved_id, ClaimStatus.APPROVED)

    first = create_master_cv_snapshot("u1", repo, store)
    # Approve the second claim and snapshot again -> a second version exists.
    repo.transition_claim(pending_id, ClaimStatus.APPROVED)
    create_master_cv_snapshot("u1", repo, store)

    # The claim was pending when version 1 was cut: it appears in NO version built
    # while unapproved, and version content is immutable after the fact.
    version_one = store.get_version("u1", first.version)
    assert version_one is not None
    blob = json.dumps(version_one.content)
    assert "Automated triage with Python" in blob
    assert "Rewrote the settlement importer" not in blob


def test_rejected_claims_never_enter_a_version(store: MasterCvSnapshotStore) -> None:
    repo = InMemoryClaimRepository()
    _, claim_ids = _seed(repo, actions=("Automated triage with Python", "Inflated claim text"))
    repo.transition_claim(claim_ids[0], ClaimStatus.APPROVED)
    repo.transition_claim(claim_ids[1], ClaimStatus.REJECTED, review_note="inflated")

    snapshot = create_master_cv_snapshot("u1", repo, store)
    assert "Inflated claim text" not in json.dumps(snapshot.content)


def test_builder_drops_unapproved_claims_even_if_a_caller_passes_them() -> None:
    repo = InMemoryClaimRepository()
    _, claim_ids = _seed(repo, actions=("Automated triage with Python",))
    # Deliberately bypass the status-filtered query: the builder must still refuse.
    pending = [repo.get_claim(claim_ids[0])]
    content = build_snapshot_content(repo.list_experiences("u1"), [c for c in pending if c])
    assert content["sections"] == {"professional_experience": [], "projects_hackathons": []}


# --- EXIT TEST: section reassignment changes render grouping ----------------------------


def test_section_reassignment_changes_render_grouping(store: MasterCvSnapshotStore) -> None:
    repo = InMemoryClaimRepository()
    experience_id, claim_ids = _seed(
        repo, experience_name="carrier-etl", section=ExperienceSection.PROJECTS_HACKATHONS
    )
    repo.transition_claim(claim_ids[0], ClaimStatus.APPROVED)

    before = create_master_cv_snapshot("u1", repo, store)
    assert [e["name"] for e in before.content["sections"]["projects_hackathons"]] == ["carrier-etl"]
    assert before.content["sections"]["professional_experience"] == []

    repo.update_experience_layout(experience_id, section=ExperienceSection.PROFESSIONAL_EXPERIENCE)
    after = create_master_cv_snapshot("u1", repo, store)

    assert after.version == before.version + 1  # the regrouping is a new version
    assert [e["name"] for e in after.content["sections"]["professional_experience"]] == [
        "carrier-etl"
    ]
    assert after.content["sections"]["projects_hackathons"] == []


def test_sort_order_orders_experiences_within_a_section(store: MasterCvSnapshotStore) -> None:
    repo = InMemoryClaimRepository()
    first_id, first_claims = _seed(repo, experience_name="First", actions=("Built A in Python",))
    second_id, second_claims = _seed(repo, experience_name="Second", actions=("Built B in Python",))
    repo.transition_claim(first_claims[0], ClaimStatus.APPROVED)
    repo.transition_claim(second_claims[0], ClaimStatus.APPROVED)
    repo.update_experience_layout(first_id, sort_order=5)
    repo.update_experience_layout(second_id, sort_order=1)

    snapshot = create_master_cv_snapshot("u1", repo, store)
    names = [e["name"] for e in snapshot.content["sections"]["professional_experience"]]
    assert names == ["Second", "First"]


# --- versioning semantics ----------------------------------------------------------------


def test_snapshot_is_idempotent_for_unchanged_content(store: MasterCvSnapshotStore) -> None:
    repo = InMemoryClaimRepository()
    _, claim_ids = _seed(repo)
    repo.transition_claim(claim_ids[0], ClaimStatus.APPROVED)

    first = create_master_cv_snapshot("u1", repo, store)
    second = create_master_cv_snapshot("u1", repo, store)
    assert (first.version, second.version) == (1, 1)
    assert store.list_versions("u1") == [1]


def test_experiences_without_approved_claims_are_omitted(store: MasterCvSnapshotStore) -> None:
    repo = InMemoryClaimRepository()
    _seed(repo, experience_name="NothingApproved")
    snapshot = create_master_cv_snapshot("u1", repo, store)
    assert snapshot.content["sections"]["professional_experience"] == []
    assert snapshot.content["sections"]["projects_hackathons"] == []
