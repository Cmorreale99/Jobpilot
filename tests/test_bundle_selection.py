"""v3.1 Increment 4 — bundle selection + single-bullet generation, end to end.

The plan's exit criteria: detect spaces → select a bundle → pick exactly one action
and one result → ``generate_bullet`` returns one grounded bullet; selecting
cross-bundle ids is refused (HTTP 409); a result-less bundle routes to the 7-option
targeted follow-up and the typed answer (the existing attestation mechanism) closes
the loop; a problem-less (leftover) story cannot select until its problem is
answered; and no path can emit an ungrounded number.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.domain.bullet import (
    UNSUPPORTED_NUMBER,
    BulletGenerationError,
    generate_bullet,
)
from app.domain.bundle_validation import (
    ASK_TARGETED_FOLLOWUP,
    MISSING_RESULT,
    SELECTED_RESULT_OUTSIDE_BUNDLE,
)
from app.domain.claims import (
    SOURCE_DRIVE,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
)
from app.domain.problem_space import BundleStatus, detect_problem_spaces
from app.domain.project_story import (
    LEFTOVER_PROBLEM_SPACE_ID,
    ProjectStory,
    StoryReviewStatus,
    component_id,
)
from app.main import create_app
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_review import (
    MISSING_PROBLEM,
    RESULT_TYPE_OPTIONS,
    SELECTION_MISSING,
    BundleSelectionError,
    StoryDecidedError,
    attest_story_component,
    generate_story_bullet,
    select_bundle_component,
)
from app.services.story_synthesis import run_story_synthesis
from app.services.validation_run_log import InMemoryValidationRunLog
from fastapi.testclient import TestClient

from tests.fixtures.problem_spaces.cooper_ai import (
    cooper_claims,
    seed_cooper_repository,
    seed_group_repository,
)

# A problem and work statements with no outcome anywhere (the follow-up case).
RESULTLESS_TEXT = (
    "Export reconciliation\n"
    "Problem: Analysts spent hours each week reconciling exports by hand.\n"
    "Built a reconciliation service in Python.\n"
    "Refactored the nightly export job."
)


def _resultless_group() -> EvidenceGroup:
    return EvidenceGroup(
        experience=ExperienceSeed(name="recon", section=ExperienceSection.PROJECTS_HACKATHONS),
        chunks=(EvidenceChunk(SOURCE_DRIVE, "recon_doc", RESULTLESS_TEXT),),
    )


@pytest.fixture
def repos() -> tuple[InMemoryClaimRepository, InMemoryProjectStoryRepository]:
    return InMemoryClaimRepository(), InMemoryProjectStoryRepository()


def _synthesized_cooper(
    repos: tuple[InMemoryClaimRepository, InMemoryProjectStoryRepository],
) -> dict[str, ProjectStory]:
    """Cooper.ai synthesized: stories keyed 'fedex' / 'pacifica' / 'leftover'."""
    claims_repo, stories = repos
    experience, _ = seed_cooper_repository(claims_repo)
    run_story_synthesis("u1", claims_repo, stories)
    keyed: dict[str, ProjectStory] = {}
    for story in stories.list_stories_for_experience("u1", experience.id):
        problem = story.content.problem_text or ""
        if "FedEx" in problem:
            keyed["fedex"] = story
        elif "manual file preparation" in problem:
            keyed["pacifica"] = story
        elif story.problem_space_id == LEFTOVER_PROBLEM_SPACE_ID:
            keyed["leftover"] = story
    assert set(keyed) == {"fedex", "pacifica", "leftover"}
    return keyed


def _action_id(story: ProjectStory, fragment: str) -> str:
    return next(a.component_id for a in story.content.actions if fragment in a.summary)


def _result_id(story: ProjectStory, fragment: str) -> str:
    return next(r.component_id for r in story.content.results if fragment in r.text)


# --- the happy path: select → ready → one grounded bullet --------------------------------


def test_select_then_generate_one_grounded_bullet(repos) -> None:  # type: ignore[no-untyped-def]
    claims_repo, stories = repos
    fedex = _synthesized_cooper(repos)["fedex"]
    action_id = _action_id(fedex, "Rebuilt the FedEx shipping fact model")
    result_id = _result_id(fedex, "195K+")

    selected = select_bundle_component(stories, claims_repo, fedex.id, action_id, result_id)
    assert selected.content.selected_action_id == action_id
    assert selected.content.selected_result_id == result_id
    assert selected.content.bundle_status == BundleStatus.READY.value
    assert selected.review_status is fedex.review_status  # selection is not a review decision

    outcome = generate_story_bullet(stories, claims_repo, fedex.id)
    assert outcome.follow_up is None
    bullet = outcome.bullet
    assert bullet is not None
    # Verbatim composition: selected action — selected result, nothing invented.
    assert bullet.text == (
        "Rebuilt the FedEx shipping fact model after the carrier schema migration — "
        "Removed 195K+ duplicate FedEx records."
    )
    assert bullet.action_candidate_id == action_id
    assert bullet.result_candidate_id == result_id
    assert bullet.claim_ids  # traced to real claims
    assert bullet.problem_space_id == fedex.problem_space_id


def test_cross_bundle_selection_is_refused_and_nothing_persists(repos) -> None:  # type: ignore[no-untyped-def]
    claims_repo, stories = repos
    keyed = _synthesized_cooper(repos)
    fedex, pacifica = keyed["fedex"], keyed["pacifica"]
    action_id = _action_id(fedex, "Rebuilt the FedEx shipping fact model")
    foreign_result = _result_id(pacifica, "warehouse refreshes")

    with pytest.raises(BundleSelectionError) as excinfo:
        select_bundle_component(stories, claims_repo, fedex.id, action_id, foreign_result)
    assert [v.code for v in excinfo.value.violations] == [SELECTED_RESULT_OUTSIDE_BUNDLE]

    unchanged = stories.get_story(fedex.id)
    assert unchanged is not None
    assert unchanged.content.selected_action_id is None
    assert unchanged.content.bundle_status == BundleStatus.REQUIRES_USER_SELECTION.value


def test_bullet_without_a_recorded_selection_is_refused(repos) -> None:  # type: ignore[no-untyped-def]
    claims_repo, stories = repos
    fedex = _synthesized_cooper(repos)["fedex"]
    with pytest.raises(BundleSelectionError) as excinfo:
        generate_story_bullet(stories, claims_repo, fedex.id)
    assert [v.code for v in excinfo.value.violations] == [SELECTION_MISSING]


def test_selection_is_review_time_work_only(repos) -> None:  # type: ignore[no-untyped-def]
    claims_repo, stories = repos
    fedex = _synthesized_cooper(repos)["fedex"]
    action_id = _action_id(fedex, "Rebuilt the FedEx shipping fact model")
    result_id = _result_id(fedex, "195K+")
    select_bundle_component(stories, claims_repo, fedex.id, action_id, result_id)
    stories.transition_story(fedex.id, StoryReviewStatus.APPROVED)

    with pytest.raises(StoryDecidedError):
        select_bundle_component(stories, claims_repo, fedex.id, action_id, result_id)
    # Generation is read-only — an approved story's bullet still generates.
    outcome = generate_story_bullet(stories, claims_repo, fedex.id)
    assert outcome.bullet is not None


# --- missing result: the 7-option follow-up, closed by the attestation mechanism ----------


def test_resultless_story_routes_to_the_followup_and_answer_closes_the_loop(repos) -> None:  # type: ignore[no-untyped-def]
    claims_repo, stories = repos
    experience, _ = seed_group_repository(claims_repo, _resultless_group())
    run_story_synthesis("u1", claims_repo, stories)
    story = stories.get_story_for_experience("u1", experience.id)
    assert story is not None
    assert story.content.bundle_status == BundleStatus.MISSING_RESULT.value

    # Selection in a result-less bundle is unreachable: it routes to the follow-up.
    action_id = _action_id(story, "reconciliation service")
    with pytest.raises(BundleSelectionError) as excinfo:
        select_bundle_component(stories, claims_repo, story.id, action_id, "r-anything")
    assert [v.code for v in excinfo.value.violations] == [MISSING_RESULT]
    assert excinfo.value.violations[0].next_action == ASK_TARGETED_FOLLOWUP

    # The bullet endpoint answers with the 7-option targeted question, not an error.
    outcome = generate_story_bullet(stories, claims_repo, story.id)
    assert outcome.bullet is None
    assert outcome.follow_up is not None
    assert outcome.follow_up["kind"] == "missing_result"
    assert outcome.follow_up["options"] == list(RESULT_TYPE_OPTIONS)
    assert len(outcome.follow_up["options"]) == 7
    assert "coverage" in outcome.follow_up["options"]
    assert "quantitative" not in outcome.follow_up["options"]

    # The typed answer (existing attestation mechanism) becomes the result candidate.
    answer = "The nightly export reconciliation now runs without manual intervention"
    attest_story_component(stories, claims_repo, story.id, "result", answer)
    attested_result_id = component_id("r", answer)
    selected = select_bundle_component(
        stories, claims_repo, story.id, action_id, attested_result_id
    )
    assert selected.content.bundle_status == BundleStatus.READY.value

    outcome = generate_story_bullet(stories, claims_repo, story.id)
    assert outcome.bullet is not None
    assert outcome.bullet.text == (
        "Built a reconciliation service in Python — "
        "The nightly export reconciliation now runs without manual intervention."
    )


def test_problemless_leftover_story_requires_the_problem_answer_first(repos) -> None:  # type: ignore[no-untyped-def]
    """The Cooper dataset-delivery case end to end: uncovered claims live in the
    leftover story; selection waits for the missing-problem answer, then the audited
    'Delivered five production Snowflake datasets' bullet generates, grounded."""
    claims_repo, stories = repos
    leftover = _synthesized_cooper(repos)["leftover"]
    action_id = _action_id(leftover, "Snowpark ingestion")
    result_id = _result_id(leftover, "five production Snowflake datasets")

    with pytest.raises(BundleSelectionError) as excinfo:
        select_bundle_component(stories, claims_repo, leftover.id, action_id, result_id)
    assert [v.code for v in excinfo.value.violations] == [MISSING_PROBLEM]

    attest_story_component(
        stories,
        claims_repo,
        leftover.id,
        "problem",
        "Client analytics and AI workflows lacked reliable production Snowflake datasets",
    )
    select_bundle_component(stories, claims_repo, leftover.id, action_id, result_id)
    outcome = generate_story_bullet(stories, claims_repo, leftover.id)
    assert outcome.bullet is not None
    assert outcome.bullet.text == (
        "Built Snowpark ingestion with validation controls and fail-safe loading patterns — "
        "Delivered five production Snowflake datasets."
    )


# --- the number gate fails closed (pure domain) --------------------------------------------


def test_generate_bullet_refuses_ungrounded_numbers() -> None:
    claims = cooper_claims(experience_id=7)
    spaces = detect_problem_spaces(7, claims)
    fedex = next(s for s in spaces if "FedEx" in s.label).bundles[0]
    action = next(c for c in fedex.action_candidates if "Rebuilt" in c.text)
    result = next(c for c in fedex.result_candidates if "195K+" in c.text)

    # Grounded: the cited chunk text carries the number → one bullet.
    bullet = generate_bullet(
        fedex,
        action.candidate_id,
        result.candidate_id,
        evidence_texts=["Removed 195K+ duplicate FedEx records."],
    )
    assert "195K+" in bullet.text

    # No evidence texts → the same number is unsupported → refuse, compose nothing.
    with pytest.raises(BulletGenerationError) as excinfo:
        generate_bullet(fedex, action.candidate_id, result.candidate_id, evidence_texts=[])
    assert UNSUPPORTED_NUMBER in {v.code for v in excinfo.value.violations}


# --- HTTP routes ---------------------------------------------------------------------------


@pytest.fixture
def client_and_stories() -> tuple[TestClient, InMemoryProjectStoryRepository, int]:
    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, _ = seed_cooper_repository(claims_repo)
    run_story_synthesis("u1", claims_repo, stories)
    app = create_app(
        settings=Settings(),
        claim_repository=claims_repo,
        story_repository=stories,
        validation_log=InMemoryValidationRunLog(),
    )
    return TestClient(app), stories, experience.id


def test_select_and_bullet_routes(client_and_stories) -> None:  # type: ignore[no-untyped-def]
    client, stories, experience_id = client_and_stories
    keyed = {
        (
            "fedex"
            if "FedEx" in (s.content.problem_text or "")
            else "pacifica"
            if "manual file preparation" in (s.content.problem_text or "")
            else "leftover"
        ): s
        for s in stories.list_stories_for_experience("u1", experience_id)
    }
    fedex = keyed["fedex"]
    action_id = _action_id(fedex, "Rebuilt the FedEx shipping fact model")
    result_id = _result_id(fedex, "195K+")

    # Bullet before selection → 409 with machine-readable violations.
    refused = client.post(f"/stories/{fedex.id}/bullet")
    assert refused.status_code == 409
    assert refused.json()["detail"]["violations"][0]["code"] == SELECTION_MISSING

    # Cross-bundle pick → 409, nothing persisted.
    foreign = _result_id(keyed["pacifica"], "warehouse refreshes")
    crossed = client.post(
        f"/stories/{fedex.id}/select",
        json={"selected_action_id": action_id, "selected_result_id": foreign},
    )
    assert crossed.status_code == 409
    assert crossed.json()["detail"]["violations"][0]["code"] == SELECTED_RESULT_OUTSIDE_BUNDLE

    # Valid selection → 200, card shows the recorded selection + ready status.
    ok = client.post(
        f"/stories/{fedex.id}/select",
        json={"selected_action_id": action_id, "selected_result_id": result_id},
    )
    assert ok.status_code == 200
    assert ok.json()["selected_action_id"] == action_id
    assert ok.json()["bundle_status"] == "ready"

    generated = client.post(f"/stories/{fedex.id}/bullet")
    assert generated.status_code == 200
    payload = generated.json()
    assert payload["follow_up"] is None
    assert payload["bullet"]["text"].startswith("Rebuilt the FedEx shipping fact model")
    assert payload["bullet"]["claim_ids"]

    assert client.post("/stories/99999/bullet").status_code == 404


def test_bullet_route_returns_the_followup_for_a_resultless_story() -> None:
    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, _ = seed_group_repository(claims_repo, _resultless_group())
    run_story_synthesis("u1", claims_repo, stories)
    story = stories.get_story_for_experience("u1", experience.id)
    assert story is not None
    app = create_app(
        settings=Settings(),
        claim_repository=claims_repo,
        story_repository=stories,
        validation_log=InMemoryValidationRunLog(),
    )
    client = TestClient(app)

    response = client.post(f"/stories/{story.id}/bullet")
    assert response.status_code == 200
    payload = response.json()
    assert payload["bullet"] is None
    assert payload["follow_up"]["kind"] == "missing_result"
    assert len(payload["follow_up"]["options"]) == 7

    # Close the loop over HTTP alone (Increment 4b — what the dashboard drives): the
    # typed answer's card component id IS the selectable candidate id, so the UI can
    # radio-select exactly what the card shows.
    answer = "The nightly export reconciliation now runs without manual intervention"
    answered = client.post(
        f"/stories/{story.id}/answer", json={"component": "result", "text": answer}
    )
    assert answered.status_code == 200
    [result_card] = answered.json()["results"]
    assert result_card["component_id"] == component_id("r", answer)

    action_id = _action_id(story, "reconciliation service")
    selected = client.post(
        f"/stories/{story.id}/select",
        json={"selected_action_id": action_id, "selected_result_id": result_card["component_id"]},
    )
    assert selected.status_code == 200
    assert selected.json()["bundle_status"] == "ready"
    generated = client.post(f"/stories/{story.id}/bullet")
    assert generated.status_code == 200
    assert generated.json()["bullet"]["text"].endswith(f"{answer}.")
