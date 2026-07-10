"""v3.1 Increment 2: a bundle with no result candidates is a follow-up, never a fill.

A problem + actions with no outcome statement anywhere must surface as
``status=missing_result`` with an empty candidate list, and
``validate_result_presence`` must route it to the targeted follow-up
(``next_action=ask_targeted_followup``) — the result slot is asked for, never
hallucinated.
"""

from __future__ import annotations

from app.domain.bundle_validation import (
    ASK_TARGETED_FOLLOWUP,
    MISSING_RESULT,
    validate_result_presence,
)
from app.domain.claims import (
    SOURCE_DRIVE,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    ResultKind,
)
from app.domain.problem_space import BundleStatus, PARBundle, detect_problem_spaces

from tests.fixtures.problem_spaces.cooper_ai import claims_from_group, cooper_claims

# A problem and two work statements — deliberately no outcome statement anywhere.
RESULTLESS_TEXT = (
    "Export reconciliation\n"
    "Problem: Analysts spent hours each week reconciling exports by hand.\n"
    "Built a reconciliation service in Python.\n"
    "Refactored the nightly export job."
)

EXPERIENCE_ID = 3


def _resultless_bundle() -> PARBundle:
    group = EvidenceGroup(
        experience=ExperienceSeed(name="recon", section=ExperienceSection.PROJECTS_HACKATHONS),
        chunks=(EvidenceChunk(SOURCE_DRIVE, "recon_doc", RESULTLESS_TEXT),),
    )
    claims = claims_from_group(group, experience_id=EXPERIENCE_ID)
    assert all(c.result_kind is ResultKind.MISSING for c in claims)

    spaces = detect_problem_spaces(EXPERIENCE_ID, claims)
    assert len(spaces) == 1
    assert len(spaces[0].bundles) == 1
    return spaces[0].bundles[0]


def test_resultless_bundle_is_missing_result_with_no_invented_candidates() -> None:
    bundle = _resultless_bundle()
    assert bundle.status is BundleStatus.MISSING_RESULT
    assert bundle.result_candidates == ()
    assert bundle.selected_result_id is None
    # The work itself is intact — only the result slot is empty.
    assert len(bundle.action_candidates) == 2
    assert "hours each week" in bundle.problem.text


def test_validate_result_presence_routes_to_the_targeted_followup() -> None:
    bundle = _resultless_bundle()
    violations = validate_result_presence(bundle)
    assert [v.code for v in violations] == [MISSING_RESULT]
    assert violations[0].next_action == ASK_TARGETED_FOLLOWUP


def test_bundle_with_results_passes_result_presence() -> None:
    claims = cooper_claims(experience_id=7)
    spaces = detect_problem_spaces(7, claims)
    fedex = next(s for s in spaces if "FedEx" in s.label).bundles[0]
    assert validate_result_presence(fedex) == []
