"""v3.1 Increment 2 regression: FedEx and Pacifica never blend into one story.

Before v3.1, ``select_story_content`` picked exactly one problem per entity and pooled
ALL of its actions and results under it — an action from one problem space could sit
beside a result from another. ``detect_problem_spaces`` separates them, every candidate
carries provenance stamps that keep it inside its space, and the alignment validator
makes a mixed-space bullet unrepresentable.
"""

from __future__ import annotations

from app.domain.bundle_validation import (
    PROBLEM_SPACE_MISMATCH,
    validate_evidence_boundary,
    validate_problem_space_alignment,
)
from app.domain.claims import Claim
from app.domain.problem_space import (
    BundleStatus,
    ProblemSpace,
    detect_problem_spaces,
    uncovered_claim_ids,
)

from tests.fixtures.problem_spaces.cooper_ai import (
    DATASET_RESULTS,
    FEDEX_RESULTS,
    PACIFICA_RESULTS,
    cooper_claims,
)

EXPERIENCE_ID = 7


def _detect() -> tuple[list[Claim], list[ProblemSpace]]:
    claims = cooper_claims(experience_id=EXPERIENCE_ID)
    return claims, detect_problem_spaces(EXPERIENCE_ID, claims)


def _space_by_problem(spaces: list[ProblemSpace], needle: str) -> ProblemSpace:
    return next(s for s in spaces if needle in s.label)


def test_fedex_and_pacifica_are_separate_problem_spaces() -> None:
    _, spaces = _detect()
    fedex = _space_by_problem(spaces, "FedEx")
    pacifica = _space_by_problem(spaces, "manual file preparation")

    assert fedex.problem_space_id != pacifica.problem_space_id
    # One distinct problem each, so one bundle each — awaiting user selection.
    assert len(fedex.bundles) == 1
    assert len(pacifica.bundles) == 1
    assert fedex.bundles[0].status is BundleStatus.REQUIRES_USER_SELECTION
    assert pacifica.bundles[0].status is BundleStatus.REQUIRES_USER_SELECTION


def test_candidates_stay_inside_their_space() -> None:
    _, spaces = _detect()
    fedex = _space_by_problem(spaces, "FedEx").bundles[0]
    pacifica = _space_by_problem(spaces, "manual file preparation").bundles[0]

    fedex_results = {c.text for c in fedex.result_candidates}
    pacifica_results = {c.text for c in pacifica.result_candidates}
    assert fedex_results == set(FEDEX_RESULTS)
    assert pacifica_results == set(PACIFICA_RESULTS)

    fedex_actions = {c.text for c in fedex.action_candidates}
    pacifica_actions = {c.text for c in pacifica.action_candidates}
    assert not fedex_actions & pacifica_actions
    assert all("FedEx" not in text for text in pacifica_actions | pacifica_results)


def test_mixed_space_components_fail_alignment() -> None:
    _, spaces = _detect()
    fedex = _space_by_problem(spaces, "FedEx").bundles[0]
    pacifica = _space_by_problem(spaces, "manual file preparation").bundles[0]

    aligned = validate_problem_space_alignment(
        fedex.problem, fedex.action_candidates[0], fedex.result_candidates[0]
    )
    assert aligned == []

    # A FedEx problem beside a Pacifica action — the blend v3 allowed — is refused.
    mixed_action = validate_problem_space_alignment(
        fedex.problem, pacifica.action_candidates[0], fedex.result_candidates[0]
    )
    assert [v.code for v in mixed_action] == [PROBLEM_SPACE_MISMATCH]

    # Same for a foreign result under an otherwise-aligned problem + action.
    mixed_result = validate_problem_space_alignment(
        fedex.problem, fedex.action_candidates[0], pacifica.result_candidates[0]
    )
    assert [v.code for v in mixed_result] == [PROBLEM_SPACE_MISMATCH]


def test_detected_components_pass_the_evidence_boundary() -> None:
    _, spaces = _detect()
    for space in spaces:
        for bundle in space.bundles:
            components = (bundle.problem, *bundle.action_candidates, *bundle.result_candidates)
            for component in components:
                assert validate_evidence_boundary(component) == []


def test_dataset_claims_without_a_bar_clearing_problem_stay_uncovered() -> None:
    """The dataset chunk's problem line clears no pain-point lexicon, so its claims
    carry no problem — they must stay honestly uncovered, never guessed into the
    FedEx or Pacifica space."""
    claims, spaces = _detect()
    uncovered = uncovered_claim_ids(EXPERIENCE_ID, claims, spaces)

    by_id = {c.id: c for c in claims}
    uncovered_results = {by_id[i].result_text for i in uncovered}
    assert set(DATASET_RESULTS) <= {t for t in uncovered_results if t}

    covered_texts: set[str] = set()
    for space in spaces:
        for bundle in space.bundles:
            covered_texts |= {c.text for c in bundle.result_candidates}
    assert not covered_texts & set(DATASET_RESULTS)
