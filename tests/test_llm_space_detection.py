"""v3.1 Increment 7 — the pluggable problem-space detector and its LLM implementation.

What matters: the detector only GROUPS the given problem statements — the domain
sanitizer drops unknown text, restores omitted problems as singleton spaces, and keeps
first membership, so grouping can never author, lose, or duplicate a problem. The LLM
grouper works by index over a fake client, fails loudly (never a silent heuristic
swap mid-run), and the same detector threads through synthesis AND eval so the
contamination invariant judges stories against the partition that built them.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.domain.problem_space import (
    HeuristicProblemSpaceDetector,
    ProblemSpaceDetectionError,
    detect_problem_spaces,
    synthesis_units,
)
from app.domain.project_story import LEFTOVER_PROBLEM_SPACE_ID
from app.llm.fake import FakeLlmClient
from app.llm.space_detection import LlmProblemSpaceDetector
from app.services.claim_repository import InMemoryClaimRepository
from app.services.problem_space_detector_factory import create_problem_space_detector
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_eval import run_story_eval
from app.services.story_synthesis import run_story_synthesis
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.fixtures.problem_spaces.cooper_ai import cooper_claims, seed_cooper_repository

MERGE_ALL = '{"groups": [[1, 2]]}'


class _StubDetector:
    """Scripted grouping for the sanitizer tests."""

    def __init__(self, groups: list[list[str]]) -> None:
        self._groups = groups

    def group_problems(self, problems):  # type: ignore[no-untyped-def]
        return self._groups


def _cooper_problems() -> list[str]:
    """Cooper's two bar-clearing problems (dataset delivery clears no lexicon)."""
    claims = cooper_claims(experience_id=7)
    spaces = detect_problem_spaces(7, claims)
    assert len(spaces) == 2
    return [space.label for space in spaces]


# --- the sanitizer: grouping is selection, never authorship -------------------------------


def test_a_merging_detector_collapses_spaces_and_unions_candidates() -> None:
    claims = cooper_claims(experience_id=7)
    fedex, pacifica = _cooper_problems()

    [space] = detect_problem_spaces(7, claims, detector=_StubDetector([[fedex, pacifica]]))
    assert len(space.bundles) == 2  # one bundle per problem, both inside the ONE space
    candidate_texts = " | ".join(c.text for c in space.bundles[0].result_candidates)
    # The merged space's candidates union both former spaces' results.
    assert "195K+" in candidate_texts
    assert "warehouse refreshes" in candidate_texts


def test_unknown_texts_are_dropped_and_omitted_problems_restored() -> None:
    claims = cooper_claims(experience_id=7)
    fedex, _pacifica = _cooper_problems()

    spaces = detect_problem_spaces(
        7, claims, detector=_StubDetector([[fedex, "an invented problem statement"]])
    )
    # The invented text vanished; the omitted Pacifica problem came back as a singleton.
    assert len(spaces) == 2
    assert {len(s.bundles) for s in spaces} == {1}


def test_duplicate_membership_keeps_the_first_group() -> None:
    claims = cooper_claims(experience_id=7)
    fedex, pacifica = _cooper_problems()

    spaces = detect_problem_spaces(
        7, claims, detector=_StubDetector([[fedex, pacifica], [pacifica]])
    )
    assert len(spaces) == 1  # the second membership was ignored, not a second space


def test_single_problem_entities_never_consult_the_detector() -> None:
    class _Exploding:
        def group_problems(self, problems):  # type: ignore[no-untyped-def]
            raise AssertionError("must not be consulted for < 2 problems")

    claims = [
        c
        for c in cooper_claims(experience_id=7)
        if "FedEx" in (c.problem_text or "") or not (c.problem_text or "").strip()
    ]
    spaces = detect_problem_spaces(7, claims, detector=_Exploding())
    assert len(spaces) == 1


# --- the LLM grouper (fake client): index grouping, loud failure ---------------------------


def test_llm_detector_groups_by_index() -> None:
    client = FakeLlmClient(responses=['{"groups": [[1, 3], [2]]}'])
    groups = LlmProblemSpaceDetector(client).group_problems(["alpha", "beta", "gamma"])
    assert groups == [["alpha", "gamma"], ["beta"]]


def test_llm_detector_drops_out_of_range_and_duplicate_indices() -> None:
    client = FakeLlmClient(responses=['{"groups": [[1, 1, 9], [2]]}'])
    groups = LlmProblemSpaceDetector(client).group_problems(["alpha", "beta"])
    assert groups == [["alpha"], ["beta"]]


def test_llm_detector_retries_malformed_json_once() -> None:
    client = FakeLlmClient(responses=["not json at all", '{"groups": [[1], [2]]}'])
    groups = LlmProblemSpaceDetector(client).group_problems(["alpha", "beta"])
    assert groups == [["alpha"], ["beta"]]


def test_llm_detector_failure_is_loud() -> None:
    client = FakeLlmClient(fail_times=5)
    with pytest.raises(ProblemSpaceDetectionError):
        LlmProblemSpaceDetector(client).group_problems(["alpha", "beta"])


# --- the factory: safe default, footgun guard ----------------------------------------------


def test_factory_defaults_to_the_heuristic() -> None:
    detector = create_problem_space_detector(Settings())
    assert isinstance(detector, HeuristicProblemSpaceDetector)


def test_factory_refuses_the_offline_fake_without_llm_enabled() -> None:
    detector = create_problem_space_detector(Settings(problem_space_llm_detection=True))
    assert isinstance(detector, HeuristicProblemSpaceDetector)


def test_factory_honors_an_injected_client() -> None:
    detector = create_problem_space_detector(
        Settings(problem_space_llm_detection=True), llm_client=FakeLlmClient()
    )
    assert isinstance(detector, LlmProblemSpaceDetector)


# --- synthesis + eval share the partition ---------------------------------------------------


def test_llm_merge_collapses_cooper_and_eval_agrees() -> None:
    """The workload fix end to end: the LLM merges Cooper's two stated problems into
    one space, single-space pooling absorbs the dataset-delivery claims (no second
    space to contaminate), and eval — run with the SAME detector — scores the merged
    story clean."""
    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, _ = seed_cooper_repository(claims_repo)
    detector = LlmProblemSpaceDetector(FakeLlmClient(handler=lambda messages, tier: MERGE_ALL))

    report = run_story_synthesis("u1", claims_repo, stories, detector=detector)

    entity_stories = stories.list_stories_for_experience("u1", experience.id)
    assert report.failed == []
    assert len(entity_stories) == 1  # was 3 under the heuristic (2 spaces + leftover)
    [story] = entity_stories
    assert story.problem_space_id != LEFTOVER_PROBLEM_SPACE_ID
    texts = " | ".join(
        [*(a.summary for a in story.content.actions), *(r.text for r in story.content.results)]
    )
    assert "Delivered five production Snowflake datasets" in texts  # pooled, not lost

    eval_report = run_story_eval("u1", stories, claims_repo, detector=detector)
    assert eval_report.cross_problem_space_contamination_count == 0


def test_detection_failure_skips_the_entity_and_keeps_existing_drafts() -> None:
    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, _ = seed_cooper_repository(claims_repo)
    log = InMemoryValidationRunLog()
    run_story_synthesis("u1", claims_repo, stories)  # heuristic first pass: 3 drafts
    before = stories.list_stories_for_experience("u1", experience.id)
    assert len(before) == 3

    failing = LlmProblemSpaceDetector(FakeLlmClient(fail_times=5))
    report = run_story_synthesis(
        "u1", claims_repo, stories, detector=failing, validation_log=log, force=True
    )

    assert report.failed == [experience.id]
    assert report.synthesized == [] and report.stale_deleted == []
    after = stories.list_stories_for_experience("u1", experience.id)
    assert [s.id for s in after] == [s.id for s in before]  # drafts untouched
    [run] = log.list_runs("u1", "story_synthesis")
    assert run.passed is False
    assert any("space_detection_failed" in line for line in run.detail)


def test_heuristic_detector_reproduces_the_default_partition() -> None:
    """Explicit heuristic detector == no detector (the refactor changed nothing)."""
    claims = cooper_claims(experience_id=7)
    default_units = synthesis_units(7, claims)
    explicit_units = synthesis_units(7, claims, detector=HeuristicProblemSpaceDetector())
    assert [
        (space.problem_space_id if space else None, [c.id for c in members])
        for space, members in default_units
    ] == [
        (space.problem_space_id if space else None, [c.id for c in members])
        for space, members in explicit_units
    ]
