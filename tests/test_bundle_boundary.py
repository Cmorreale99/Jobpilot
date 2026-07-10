"""v3.1 Increment 2: selection cannot reach outside its bundle, evidence cannot
cross problem spaces or projects.

``validate_bundle_selection`` refuses ids that are not candidates of the chosen
bundle; ``validate_evidence_boundary`` reads the provenance stamps constructed at
detection, so a candidate assembled from claims across spaces or entities is caught
from the candidate alone.
"""

from __future__ import annotations

from dataclasses import replace

from app.domain.bundle_validation import (
    CROSS_PROBLEM_SPACE_CONTAMINATION,
    CROSS_PROJECT_CONTAMINATION,
    SELECTED_ACTION_OUTSIDE_BUNDLE,
    SELECTED_RESULT_OUTSIDE_BUNDLE,
    validate_bundle_selection,
    validate_evidence_boundary,
)
from app.domain.problem_space import PARBundle, detect_problem_spaces

from tests.fixtures.problem_spaces.cooper_ai import cooper_claims

EXPERIENCE_ID = 7


def _bundles() -> tuple[PARBundle, PARBundle]:
    claims = cooper_claims(experience_id=EXPERIENCE_ID)
    spaces = detect_problem_spaces(EXPERIENCE_ID, claims)
    fedex = next(s for s in spaces if "FedEx" in s.label).bundles[0]
    pacifica = next(s for s in spaces if "manual file preparation" in s.label).bundles[0]
    return fedex, pacifica


def test_selection_inside_the_bundle_passes() -> None:
    fedex, _ = _bundles()
    violations = validate_bundle_selection(
        fedex,
        fedex.action_candidates[0].candidate_id,
        fedex.result_candidates[0].candidate_id,
    )
    assert violations == []


def test_action_from_another_bundle_is_refused() -> None:
    fedex, pacifica = _bundles()
    violations = validate_bundle_selection(
        fedex,
        pacifica.action_candidates[0].candidate_id,
        fedex.result_candidates[0].candidate_id,
    )
    assert [v.code for v in violations] == [SELECTED_ACTION_OUTSIDE_BUNDLE]


def test_result_from_another_bundle_is_refused() -> None:
    fedex, pacifica = _bundles()
    violations = validate_bundle_selection(
        fedex,
        fedex.action_candidates[0].candidate_id,
        pacifica.result_candidates[0].candidate_id,
    )
    assert [v.code for v in violations] == [SELECTED_RESULT_OUTSIDE_BUNDLE]


def test_unknown_ids_are_refused_on_both_slots() -> None:
    fedex, _ = _bundles()
    violations = validate_bundle_selection(fedex, "a-nonexistent", "r-nonexistent")
    assert {v.code for v in violations} == {
        SELECTED_ACTION_OUTSIDE_BUNDLE,
        SELECTED_RESULT_OUTSIDE_BUNDLE,
    }


def test_cross_space_evidence_contamination_is_detected() -> None:
    fedex, pacifica = _bundles()
    contaminated = replace(
        fedex.action_candidates[0],
        evidence_problem_space_ids=(fedex.problem_space_id, pacifica.problem_space_id),
    )
    violations = validate_evidence_boundary(contaminated)
    assert [v.code for v in violations] == [CROSS_PROBLEM_SPACE_CONTAMINATION]


def test_cross_project_evidence_contamination_is_detected() -> None:
    fedex, _ = _bundles()
    contaminated = replace(
        fedex.result_candidates[0],
        evidence_experience_ids=(EXPERIENCE_ID, EXPERIENCE_ID + 1),
    )
    violations = validate_evidence_boundary(contaminated)
    assert [v.code for v in violations] == [CROSS_PROJECT_CONTAMINATION]
