"""Action/result causal-integrity tests (MASTER CV REPAIR §4.14, §5.6, §9, §16.11).

Source truth: sharing a problem space is NOT proof of an action→result relationship.
Two actions and two results in the same broad space with only specific valid pairings:
the invalid cross-pairings must be unselectable (409, machine-readable), the valid
pairings selectable, and an unknown relationship stays unknown — never published as
implied causality.
"""

from __future__ import annotations

import pytest
from app.domain.bundle_validation import (
    RELATIONSHIP_DIRECT,
    RELATIONSHIP_UNKNOWN,
    RELATIONSHIP_USER_ATTESTED,
    UNSUPPORTED_PAIRING,
    pairing_relationship,
)
from app.domain.claims import (
    SOURCE_DRIVE,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    CostDimension,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    Inefficiency,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_review import (
    BundleSelectionError,
    generate_story_bullet,
    select_bundle_component,
)
from app.services.story_synthesis import run_story_synthesis

USER = "u1"


def _paired_claim(topic: str, action: str, result: str, chunk_ref: str) -> StorableClaim:
    """One claim whose action and result came from the same source statement pair."""
    chunk = EvidenceChunk(SOURCE_DRIVE, chunk_ref, f"{action}\n{result}")
    problem = f"The {topic} flow cost analysts hours of manual rework every single week"
    return StorableClaim(
        draft=DraftClaim(
            action_text=action,
            action_tools=("Python",),
            problem_text=problem,
            problem_cost_dimension=CostDimension.TIME,
            problem_inefficiency=Inefficiency.MANUAL,
            result_text=result,
            result_kind=ResultKind.QUANTIFIED,
            result_metric_json={
                "resolves": "time",
                "metric_text": result.split()[-2] + " " + result.split()[-1],
            },
            evidence=(
                ClaimEvidenceRef(chunk=chunk, field=ClaimField.PROBLEM),
                ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),
                ClaimEvidenceRef(chunk=chunk, field=ClaimField.RESULT, outcome_quote=result),
            ),
        ),
        status=ClaimStatus.PENDING_REVIEW,
        result_status=ResultStatus.VERIFIED,
    )


@pytest.fixture
def story_setup():
    """One entity, one broad problem space, TWO independent action→result pairs."""
    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience = claims_repo.upsert_experience(
        USER,
        ExperienceSeed(name="Freight ETL", section=ExperienceSection.PROJECTS_HACKATHONS),
    )
    # Same topic vocabulary so the heuristic space detector pools them into ONE space.
    claims_repo.replace_unreviewed_claims(
        USER,
        experience.id,
        [
            _paired_claim(
                "freight billing",
                "Rebuilt the freight billing parser with Python",
                "Cut freight parsing failures by 90%",
                "doc-a",
            ),
            _paired_claim(
                "freight billing",
                "Automated the freight billing audit exports with Python",
                "Saved the freight team 12 hours weekly",
                "doc-b",
            ),
        ],
    )
    run_story_synthesis(USER, claims_repo, stories)
    story = stories.get_story_for_experience(USER, experience.id)
    assert story is not None, "synthesis produced no story"
    return claims_repo, stories, story


def _component_ids(story) -> tuple[dict[str, str], dict[str, str]]:
    actions = {a.summary: a.component_id for a in story.content.actions}
    results = {r.text: r.component_id for r in story.content.results}
    return actions, results


def test_valid_same_claim_pairing_is_selectable(story_setup) -> None:  # type: ignore[no-untyped-def]
    claims_repo, stories, story = story_setup
    actions, results = _component_ids(story)
    action_id = actions["Rebuilt the freight billing parser with Python"]
    result_id = results["Cut freight parsing failures by 90%"]

    selected = select_bundle_component(stories, claims_repo, story.id, action_id, result_id)
    assert selected.content.selected_action_id == action_id
    outcome = generate_story_bullet(stories, claims_repo, story.id)
    assert outcome.bullet is not None


def test_cross_claim_pairing_in_same_space_is_unselectable(story_setup) -> None:  # type: ignore[no-untyped-def]
    """§16.11: same problem space is NOT a relationship — the cross-pair must refuse."""
    claims_repo, stories, story = story_setup
    actions, results = _component_ids(story)
    action_id = actions["Rebuilt the freight billing parser with Python"]
    foreign_result = results["Saved the freight team 12 hours weekly"]

    with pytest.raises(BundleSelectionError) as excinfo:
        select_bundle_component(stories, claims_repo, story.id, action_id, foreign_result)
    assert [v.code for v in excinfo.value.violations] == [UNSUPPORTED_PAIRING]

    # Nothing persisted under the guess.
    unchanged = stories.get_story(story.id)
    assert unchanged is not None
    assert unchanged.content.selected_action_id is None


def test_pairing_relationship_derivation() -> None:
    evidence = {1: {10, 11}, 2: {20}, 3: {11, 30}}
    assert pairing_relationship((1,), (1,), evidence) == RELATIONSHIP_DIRECT, (
        "same claim = explicit source linkage"
    )
    assert pairing_relationship((1,), (2,), evidence) == RELATIONSHIP_UNKNOWN, (
        "distinct claims with disjoint evidence stay unknown"
    )
    assert pairing_relationship((1,), (), evidence, result_attested=True) == (
        RELATIONSHIP_USER_ATTESTED
    )
