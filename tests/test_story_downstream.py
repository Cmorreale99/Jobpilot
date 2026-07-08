"""V3 Phase 5a — the Story→MasterCv adapter and the snapshot-kind dispatcher (§6).

The approved-story snapshot is the only input to matching/tailoring/outreach/prep. Each
story component becomes one ParClaim addressed by its stable component id, and carries its
cited evidence for the downstream number gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.master_cv_snapshot import SNAPSHOT_KIND, StoredSnapshot
from app.domain.story_snapshot import (
    SOURCE_APPROVED_STORY,
    STORY_SNAPSHOT_KIND,
    master_cv_from_any_snapshot,
    story_master_cv_from_snapshot,
)
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_snapshot import create_story_snapshot

from tests.conftest import seed_approved_story

_ACTION_ONE = "Re-architected the settlement pipeline over Kafka in Python"
_ACTION_TWO = "Built streaming settlement reconciliation services with SQL and Python"
_RESULT = "Cut settlement batch runtime by 70%"


def _approved_story_snapshot() -> StoredSnapshot:
    claim_repo = InMemoryClaimRepository()
    story_repo = InMemoryProjectStoryRepository()
    seed_approved_story(claim_repo, story_repo)
    return create_story_snapshot("u1", story_repo, claim_repo, InMemorySnapshotStore())


def test_adapter_maps_components_to_component_addressed_par_claims() -> None:
    master_cv = story_master_cv_from_snapshot(_approved_story_snapshot())

    # Two Actions (action-only) + one Result (action — result) = three ParClaims.
    assert len(master_cv.claims) == 3
    assert all(c.source_type == SOURCE_APPROVED_STORY for c in master_cv.claims)
    # Every component is addressed by its stable component id, never a raw claim id.
    assert all(c.source_ref.startswith("story:") for c in master_cv.claims)
    assert all(len(c.source_ref.split(":")) == 3 for c in master_cv.claims)

    result_claims = [c for c in master_cv.claims if c.result]
    assert len(result_claims) == 1
    result = result_claims[0]
    assert result.result == _RESULT
    assert result.action == _ACTION_ONE  # the lead action pairs with the Result
    assert result.problem and result.problem.startswith("The settlement team")

    action_claims = [c for c in master_cv.claims if not c.result]
    assert {c.action for c in action_claims} == {_ACTION_ONE, _ACTION_TWO}


def test_adapter_carries_cited_evidence_for_the_number_gate() -> None:
    master_cv = story_master_cv_from_snapshot(_approved_story_snapshot())
    result = next(c for c in master_cv.claims if c.result)
    # evidence_text is the CITED chunk, so the number gate grounds against evidence, not
    # the (possibly composed) component text.
    assert "70%" in result.evidence_text
    assert _RESULT in result.evidence_text


def test_adapter_version_matches_the_snapshot() -> None:
    snapshot = _approved_story_snapshot()
    assert story_master_cv_from_snapshot(snapshot).version == snapshot.version


def test_dispatcher_routes_story_and_claim_snapshots() -> None:
    story_snapshot = _approved_story_snapshot()
    assert story_snapshot.content["snapshot_of"] == STORY_SNAPSHOT_KIND
    assert master_cv_from_any_snapshot(story_snapshot).claims  # routed to the story adapter

    # A legacy claim-shaped version still adapts through the claim path.
    claim_content = {
        "snapshot_of": SNAPSHOT_KIND,
        "version": 1,
        "sections": {
            "professional_experience": [
                {
                    "experience_id": 1,
                    "name": "Ledgerline",
                    "claims": [{"claim_id": 7, "action": "Built the ledger", "result": None}],
                }
            ],
            "projects_hackathons": [],
        },
    }
    claim_snapshot = StoredSnapshot(
        user_id="u1",
        version=1,
        content=claim_content,
        content_hash="x",
        created_at=datetime(2026, 7, 7, tzinfo=UTC),
    )
    adapted = master_cv_from_any_snapshot(claim_snapshot)
    assert [c.source_ref for c in adapted.claims] == ["claim:7"]
