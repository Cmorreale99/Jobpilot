"""LLM-backed story synthesis against the fake client (V3 Phase 5b).

What matters: the model may compose the Problem and group Actions, but composition is
contained by grounding — an ungrounded number or a too-vague Problem is dropped, Results
stay verbatim, and any LLM failure is loud (StorySynthesisError), never a silent degrade.
"""

from __future__ import annotations

import json

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
from app.domain.project_story import StoryReviewStatus
from app.domain.validation_runs import KIND_STORY_SYNTHESIS
from app.llm.fake import FakeLlmClient
from app.llm.story_synthesis import LlmStorySynthesizer
from app.llm.types import ModelTier
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_synthesis import run_story_synthesis
from app.services.validation_run_log import InMemoryValidationRunLog

_PROBLEM = "The billing team reconciled invoices by hand for many hours every week"
_ACTION_ONE = "Automated the billing reconciliation in Python"
_RESULT = "Cut billing reconciliation time by 70%"
_ACTION_TWO = "Built the billing export validator with SQL"


def _repo_with_entity() -> tuple[InMemoryClaimRepository, int, list[int]]:
    repo = InMemoryClaimRepository()
    experience = repo.upsert_experience(
        "u1", ExperienceSeed(name="Billing", section=ExperienceSection.PROJECTS_HACKATHONS)
    )
    full_chunk = EvidenceChunk(
        SOURCE_DRIVE, "drv-bill-1", "\n".join((_PROBLEM, _ACTION_ONE, _RESULT))
    )
    action_chunk = EvidenceChunk(SOURCE_DRIVE, "drv-bill-2", _ACTION_TWO)
    inserted = repo.replace_unreviewed_claims(
        "u1",
        experience.id,
        [
            StorableClaim(
                draft=DraftClaim(
                    action_text=_ACTION_ONE,
                    action_tools=("Python",),
                    problem_text=_PROBLEM,
                    problem_cost_dimension=CostDimension.TIME,
                    problem_inefficiency=Inefficiency.MANUAL,
                    result_text=_RESULT,
                    result_kind=ResultKind.QUANTIFIED,
                    result_metric_json={"resolves": "time", "metric_text": "70%"},
                    evidence=(
                        ClaimEvidenceRef(chunk=full_chunk, field=ClaimField.PROBLEM),
                        ClaimEvidenceRef(chunk=full_chunk, field=ClaimField.ACTION),
                        ClaimEvidenceRef(
                            chunk=full_chunk, field=ClaimField.RESULT, outcome_quote=_RESULT
                        ),
                    ),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.VERIFIED,
            ),
            StorableClaim(
                draft=DraftClaim(
                    action_text=_ACTION_TWO,
                    action_tools=("SQL",),
                    evidence=(ClaimEvidenceRef(chunk=action_chunk, field=ClaimField.ACTION),),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
            ),
        ],
    )
    return repo, experience.id, [c.id for c in inserted]


def _reply(problem: dict | None, actions: list[dict]) -> str:
    return json.dumps({"problem": problem, "actions": actions})


def test_composes_problem_groups_actions_and_keeps_results_verbatim() -> None:
    repo, experience_id, ids = _repo_with_entity()
    composed = "Billing analysts spent many hours each week reconciling invoices by hand"
    reply = _reply(
        {"text": composed, "claim_ids": [ids[0]]},
        [
            {
                "summary": "Automated the reconciliation workflow",
                "claim_ids": ids,
                "tools": ["Python"],
            }
        ],
    )
    content = LlmStorySynthesizer(FakeLlmClient([reply])).synthesize(
        experience_id, repo.list_claims("u1"), repo.get_evidence, experience_name="Billing"
    )

    assert content.problem_text == composed  # authored, not a verbatim claim
    assert content.problem_refs == (ids[0],)
    assert len(content.actions) == 1
    assert content.actions[0].summary == "Automated the reconciliation workflow"
    assert content.actions[0].claim_ids == tuple(ids)  # grouped both claims
    # Results are NOT composed — selected verbatim by the heuristic path.
    assert [r.text for r in content.results] == [_RESULT]


def test_deep_tier_is_used() -> None:
    repo, experience_id, ids = _repo_with_entity()
    reply = _reply(None, [{"summary": _ACTION_ONE, "claim_ids": [ids[0]], "tools": []}])
    client = FakeLlmClient([reply])
    LlmStorySynthesizer(client).synthesize(experience_id, repo.list_claims("u1"), repo.get_evidence)
    assert client.calls[0].tier is ModelTier.DEEP


def test_drops_composed_problem_with_an_ungrounded_number() -> None:
    repo, experience_id, ids = _repo_with_entity()
    reply = _reply(
        {
            "text": "Manual billing errors cost the team 99% of every close cycle",
            "claim_ids": [ids[0]],
        },
        [{"summary": _ACTION_ONE, "claim_ids": [ids[0]], "tools": ["Python"]}],
    )
    content = LlmStorySynthesizer(FakeLlmClient([reply])).synthesize(
        experience_id, repo.list_claims("u1"), repo.get_evidence
    )
    assert content.problem_text is None  # 99% is in no cited evidence → dropped
    assert content.problem_refs == ()


def test_drops_vague_composed_problem_below_the_specificity_bar() -> None:
    repo, experience_id, ids = _repo_with_entity()
    reply = _reply(
        {"text": "billing bug", "claim_ids": [ids[0]]},
        [{"summary": _ACTION_ONE, "claim_ids": [ids[0]], "tools": ["Python"]}],
    )
    content = LlmStorySynthesizer(FakeLlmClient([reply])).synthesize(
        experience_id, repo.list_claims("u1"), repo.get_evidence
    )
    assert content.problem_text is None


def test_drops_composed_action_with_an_ungrounded_number() -> None:
    repo, experience_id, ids = _repo_with_entity()
    reply = _reply(
        None,
        [
            {
                "summary": "Automated 5 separate reconciliation pipelines",
                "claim_ids": [ids[1]],
                "tools": [],
            },
            {"summary": _ACTION_ONE, "claim_ids": [ids[0]], "tools": ["Python"]},
        ],
    )
    content = LlmStorySynthesizer(FakeLlmClient([reply])).synthesize(
        experience_id, repo.list_claims("u1"), repo.get_evidence
    )
    # The "5" action cites claim 2 whose evidence never mentions 5 → dropped; the other stays.
    assert [a.summary for a in content.actions] == [_ACTION_ONE]


def test_drops_actions_and_problem_refs_that_cite_unknown_ids() -> None:
    repo, experience_id, ids = _repo_with_entity()
    reply = _reply(
        {
            "text": "Billing analysts reconciled invoices by hand for many hours weekly",
            "claim_ids": [9999],
        },
        [{"summary": _ACTION_ONE, "claim_ids": [9999], "tools": []}],
    )
    content = LlmStorySynthesizer(FakeLlmClient([reply])).synthesize(
        experience_id, repo.list_claims("u1"), repo.get_evidence
    )
    assert content.problem_text is None  # no valid supporting refs
    assert content.actions == ()  # the only action cited a non-existent claim


def test_run_story_synthesis_with_llm_persists_a_pending_review_story() -> None:
    repo, experience_id, ids = _repo_with_entity()
    stories = InMemoryProjectStoryRepository()
    log = InMemoryValidationRunLog()
    composed = "Billing analysts spent many hours each week reconciling invoices by hand"
    reply = _reply(
        {"text": composed, "claim_ids": [ids[0]]},
        [
            {
                "summary": "Automated the reconciliation workflow",
                "claim_ids": ids,
                "tools": ["Python"],
            }
        ],
    )
    synthesizer = LlmStorySynthesizer(FakeLlmClient([reply]))

    report = run_story_synthesis("u1", repo, stories, synthesizer=synthesizer, validation_log=log)

    assert report.synthesized == [experience_id]
    assert report.failed == []
    story = stories.get_story_for_experience("u1", experience_id)
    assert story is not None
    assert story.review_status is StoryReviewStatus.PENDING_REVIEW
    assert story.content.problem_text == composed


def test_run_story_synthesis_records_a_loud_failure_and_skips() -> None:
    repo, experience_id, _ = _repo_with_entity()
    stories = InMemoryProjectStoryRepository()
    log = InMemoryValidationRunLog()
    synthesizer = LlmStorySynthesizer(FakeLlmClient(default_text="not json at all"))

    report = run_story_synthesis("u1", repo, stories, synthesizer=synthesizer, validation_log=log)

    assert report.failed == [experience_id]
    assert report.synthesized == []
    assert stories.get_story_for_experience("u1", experience_id) is None  # nothing persisted
    runs = log.list_runs("u1", KIND_STORY_SYNTHESIS)
    assert len(runs) == 1 and runs[0].passed is False
    assert any("synthesis_failed" in line for line in runs[0].detail)
