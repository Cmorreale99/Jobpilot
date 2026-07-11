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


# --- Increment 8: recorded partitions (the drift fix) --------------------------------------


class _CountingDetector:
    """Scripted groupings, consumed per call — counts consultations."""

    def __init__(self, groupings: list[list[list[str]]]) -> None:
        self._groupings = list(groupings)
        self.calls = 0

    def group_problems(self, problems):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self._groupings.pop(0)


def test_grouping_fingerprint_is_set_based_and_version_namespaced() -> None:
    from app.domain.problem_space import grouping_fingerprint

    base = grouping_fingerprint(["Alpha problem", "beta problem"])
    assert grouping_fingerprint(["beta problem", "Alpha problem"]) == base  # order-free
    assert grouping_fingerprint(["  alpha   PROBLEM ", "Beta Problem"]) == base  # normalized
    assert grouping_fingerprint(["alpha problem", "gamma problem"]) != base
    assert grouping_fingerprint(["Alpha problem", "beta problem"], version="v2") != base


def test_persisted_detector_records_once_and_replays_forever() -> None:
    from app.domain.problem_space import PersistedGroupingDetector
    from app.services.problem_space_grouping import InMemoryGroupingStore

    store = InMemoryGroupingStore()
    inner = _CountingDetector([[["alpha problem", "beta problem"]]])
    first = PersistedGroupingDetector(inner, store, "u1")
    assert first.group_problems(["alpha problem", "beta problem"]) == [
        ["alpha problem", "beta problem"]
    ]
    assert inner.calls == 1

    # A fresh wrapper over the same store replays — the inner detector is never asked
    # again, and the replay maps onto the CURRENT text variants (casing drifted).
    replaying = PersistedGroupingDetector(_CountingDetector([]), store, "u1")
    assert replaying.group_problems(["Alpha Problem", "Beta Problem"]) == [
        ["Alpha Problem", "Beta Problem"]
    ]

    # A changed problem set is a new fingerprint — consult again.
    fresh_inner = _CountingDetector([[["alpha problem"], ["gamma problem"]]])
    changed = PersistedGroupingDetector(fresh_inner, store, "u1")
    changed.group_problems(["alpha problem", "gamma problem"])
    assert fresh_inner.calls == 1

    # A version bump never replays recordings made under old semantics.
    versioned_inner = _CountingDetector([[["alpha problem"], ["beta problem"]]])
    versioned = PersistedGroupingDetector(versioned_inner, store, "u1", version="v2")
    assert versioned.group_problems(["alpha problem", "beta problem"]) == [
        ["alpha problem"],
        ["beta problem"],
    ]
    assert versioned_inner.calls == 1


@pytest.mark.parametrize("kind", ["in_memory", "sql"])
def test_grouping_store_round_trip_and_first_write_wins(kind: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import sqlalchemy as sa
    from app.db.base import Base
    from app.db.problem_space_grouping_store import SqlGroupingStore
    from app.db.session import create_session_factory
    from app.services.problem_space_grouping import InMemoryGroupingStore

    if kind == "in_memory":
        store = InMemoryGroupingStore()
    else:
        engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'groupings.db'}")
        Base.metadata.create_all(engine)
        store = SqlGroupingStore(create_session_factory(engine))

    assert store.get("u1", "fp-1") is None
    store.put("u1", "fp-1", [["a", "b"], ["c"]])
    assert store.get("u1", "fp-1") == [["a", "b"], ["c"]]
    store.put("u1", "fp-1", [["a"], ["b"], ["c"]])  # first write wins
    assert store.get("u1", "fp-1") == [["a", "b"], ["c"]]
    assert store.get("u2", "fp-1") is None  # per-user


def test_recorded_partition_kills_drift_between_synthesis_and_eval() -> None:
    """The live-verified failure mode, reproduced and fixed: the fake LLM would group
    DIFFERENTLY on a second call, but the recorded partition replays — eval judges the
    partition that built the stories (contamination 0) and the LLM is asked exactly
    once for the unchanged problem set."""
    from app.domain.problem_space import PersistedGroupingDetector
    from app.services.problem_space_grouping import InMemoryGroupingStore

    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, _ = seed_cooper_repository(claims_repo)
    # First call merges Cooper's two problems; a second call WOULD split them (drift).
    client = FakeLlmClient(responses=[MERGE_ALL, '{"groups": [[1], [2]]}'])
    detector = PersistedGroupingDetector(
        LlmProblemSpaceDetector(client), InMemoryGroupingStore(), "u1", version="llm-v1"
    )

    run_story_synthesis("u1", claims_repo, stories, detector=detector)
    eval_report = run_story_eval("u1", stories, claims_repo, detector=detector)

    assert eval_report.cross_problem_space_contamination_count == 0
    assert len(stories.list_stories_for_experience("u1", experience.id)) == 1
    assert len(client.calls) == 1  # synthesis consulted; eval replayed the recording


def test_factory_wraps_the_llm_detector_when_given_a_store() -> None:
    from app.domain.problem_space import PersistedGroupingDetector
    from app.services.problem_space_grouping import InMemoryGroupingStore

    store = InMemoryGroupingStore()
    wrapped = create_problem_space_detector(
        Settings(problem_space_llm_detection=True),
        llm_client=FakeLlmClient(),
        grouping_store=store,
        user_id="u1",
    )
    assert isinstance(wrapped, PersistedGroupingDetector)
    # The heuristic path is never wrapped — a heuristic partition must not be
    # replayed as if the LLM had produced it.
    heuristic = create_problem_space_detector(Settings(), grouping_store=store, user_id="u1")
    assert isinstance(heuristic, HeuristicProblemSpaceDetector)


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
