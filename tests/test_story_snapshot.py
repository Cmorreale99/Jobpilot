"""V3 Phase 4 — the story-shaped Master CV snapshot (T11/T12/T13/T14 render halves).

The render unit is the approved story. This file covers the pure builder (bullets, the
render-time resume-ready gate, the duplicate-metric refusal) and the service that resolves
approved stories into it (evidenced + attested components, the number gate).
"""

from __future__ import annotations

import pytest
from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_USER_ATTESTATION,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.domain.project_story import StoryContent, StoryReviewStatus, select_story_content
from app.domain.story_snapshot import (
    STORY_SNAPSHOT_KIND,
    DuplicateMetricError,
    RenderableStory,
    build_story_snapshot_content,
    derive_bullets,
)
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_snapshot import create_story_snapshot

from tests.live_corpus import SHARED_OUTCOME_QUOTE, build_live_corpus

# --- the pure builder ---------------------------------------------------------------------


def _renderable(
    experience_id: int,
    *,
    section: ExperienceSection = ExperienceSection.PROJECTS_HACKATHONS,
    problem: dict | None = None,
    actions: tuple[dict, ...] = (),
    results: tuple[dict, ...] = (),
    sort_order: int = 0,
    name: str = "proj",
) -> RenderableStory:
    return RenderableStory(
        experience_id=experience_id,
        story_id=experience_id,
        name=name,
        section=section.value,
        sort_order=sort_order,
        problem=problem,
        actions=actions,
        results=results,
        bullets=tuple(derive_bullets(actions, results)),
    )


def _action(text: str, claim_id: int) -> dict:
    return {"component_id": f"a-{claim_id}", "text": text, "claim_ids": [claim_id]}


def _result(text: str, claim_id: int, *, quote: str | None = None) -> dict:
    return {
        "component_id": f"r-{claim_id}",
        "text": text,
        "claim_ids": [claim_id],
        "outcome_quote": quote,
    }


def test_bullets_are_actions_then_results_verbatim() -> None:
    bullets = derive_bullets(
        (_action("Built the ETL in Python", 1),),
        (_result("Cut runtime from 10 hours to 2 hours", 2),),
    )
    assert [b["text"] for b in bullets] == [
        "Built the ETL in Python.",  # a period is added; the text is otherwise verbatim
        "Cut runtime from 10 hours to 2 hours.",
    ]
    # Each bullet still traces to its component + claims (T13).
    assert bullets[0]["component_id"] == "a-1" and bullets[0]["claim_ids"] == [1]
    assert bullets[1]["component_id"] == "r-2" and bullets[1]["claim_ids"] == [2]


def test_incomplete_story_is_dropped_by_the_render_gate() -> None:
    """T12: a story missing any of Problem / Action / Result never renders."""
    no_problem = _renderable(1, actions=(_action("Did work", 1),), results=(_result("Won", 2),))
    no_action = _renderable(
        2, problem={"text": "real problem", "claim_ids": [3]}, results=(_result("Won", 4),)
    )
    no_result = _renderable(
        3, problem={"text": "real problem", "claim_ids": [5]}, actions=(_action("Did work", 6),)
    )
    complete = _renderable(
        4,
        problem={"text": "real problem", "claim_ids": [7]},
        actions=(_action("Did work", 8),),
        results=(_result("Won the thing", 9),),
    )

    content = build_story_snapshot_content([no_problem, no_action, no_result, complete])

    rendered = content["sections"][ExperienceSection.PROJECTS_HACKATHONS.value]
    assert [e["experience_id"] for e in rendered] == [4]  # only the complete story


def test_duplicate_outcome_span_across_two_stories_refuses() -> None:
    """T11/T10 render half: the same span under two stories refuses the whole snapshot."""
    one = _renderable(
        1,
        problem={"text": "hackathon problem", "claim_ids": [1]},
        actions=(_action("Built the app", 2),),
        results=(_result(SHARED_OUTCOME_QUOTE, 3, quote=SHARED_OUTCOME_QUOTE),),
    )
    two = _renderable(
        2,
        problem={"text": "portfolio problem", "claim_ids": [4]},
        actions=(_action("Wrote the case study", 5),),
        results=(_result(SHARED_OUTCOME_QUOTE, 6, quote=SHARED_OUTCOME_QUOTE),),
    )
    with pytest.raises(DuplicateMetricError) as excinfo:
        build_story_snapshot_content([one, two])
    assert excinfo.value.spans[0][1] == (1, 2)  # both experiences named


def test_sections_group_and_order_by_sort_order() -> None:
    a = _renderable(
        1,
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        problem={"text": "p", "claim_ids": [1]},
        actions=(_action("x", 2),),
        results=(_result("y1", 3),),
        sort_order=2,
        name="role-b",
    )
    b = _renderable(
        2,
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        problem={"text": "p", "claim_ids": [4]},
        actions=(_action("x", 5),),
        results=(_result("y2", 6),),
        sort_order=1,
        name="role-a",
    )
    content = build_story_snapshot_content([a, b])
    assert content["snapshot_of"] == STORY_SNAPSHOT_KIND
    professional = content["sections"][ExperienceSection.PROFESSIONAL_EXPERIENCE.value]
    assert [e["name"] for e in professional] == ["role-a", "role-b"]  # sort_order asc
    assert content["sections"][ExperienceSection.PROJECTS_HACKATHONS.value] == []


# --- the service: resolving approved stories ----------------------------------------------


def _approved_massdep_snapshot() -> dict:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    massdep = corpus.entity("MassDEP")
    content = select_story_content(massdep.id, corpus.claims_for("MassDEP"))
    story = stories.upsert_draft(corpus.user_id, massdep.id, content)
    stories.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    stories.transition_story(story.id, StoryReviewStatus.APPROVED)
    snapshot = create_story_snapshot(
        corpus.user_id, stories, corpus.repository, InMemorySnapshotStore()
    )
    return snapshot.content


def test_service_renders_only_approved_stories() -> None:
    content = _approved_massdep_snapshot()
    names = [e["name"] for section in content["sections"].values() for e in section]
    assert names == ["MassDEP"]  # the one approved story; the other 14 pending never render
    [entry] = content["sections"][ExperienceSection.PROFESSIONAL_EXPERIENCE.value]
    assert entry["bullets"]  # action + result bullets present
    assert all(b["claim_ids"] for b in entry["bullets"])  # every bullet traces to claims (T13)


def test_attested_result_renders_and_problem_is_not_bulleted() -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    engine = corpus.entity("investment-decision-workflow-engine")
    content = select_story_content(engine.id, corpus.claims_for(engine.name))
    story = stories.upsert_draft(corpus.user_id, engine.id, content)
    # The engine has an evidenced Result but no problem-space Problem — attest one.
    corpus.repository.upsert_evidence(
        corpus.user_id,
        EvidenceChunk(
            SOURCE_USER_ATTESTATION,
            f"story:{story.id}:problem",
            "Analysts screened investment candidates by hand every quarter",
        ),
    )
    stories.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    stories.transition_story(story.id, StoryReviewStatus.APPROVED)

    snapshot = create_story_snapshot(
        corpus.user_id, stories, corpus.repository, InMemorySnapshotStore()
    )
    [entry] = snapshot.content["sections"][ExperienceSection.PROJECTS_HACKATHONS.value]
    bullet_text = " ".join(b["text"] for b in entry["bullets"])
    # The attested Problem gates the story but is never a bullet.
    assert "screened investment candidates by hand" not in bullet_text
    assert entry["problem"]["text"].startswith("Analysts screened")
    # The evidenced Result is rendered.
    assert "40 seconds to 4 seconds" in bullet_text


def test_force_approved_incomplete_story_never_renders() -> None:
    """T12 service half: a story force-approved past the API gate is dropped at render."""
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    # personal-knowledge-os: 28 claims, only one-line code-bug 'Problems' (fail the bar),
    # zero Results — never resume-ready, but force it through the state machine anyway.
    pkos = corpus.entity("personal-knowledge-os")
    content = select_story_content(pkos.id, corpus.claims_for("personal-knowledge-os"))
    story = stories.upsert_draft(corpus.user_id, pkos.id, content)
    stories.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    stories.transition_story(story.id, StoryReviewStatus.APPROVED)  # bypasses the readiness gate

    snapshot = create_story_snapshot(
        corpus.user_id, stories, corpus.repository, InMemorySnapshotStore()
    )
    assert all(section == [] for section in snapshot.content["sections"].values())


def test_render_refuses_a_bullet_with_an_ungrounded_number() -> None:
    """T14 render half: a bullet number absent from cited evidence drops the story."""
    repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience = repo.upsert_experience(
        "u1", ExperienceSeed(name="etl", section=ExperienceSection.PROJECTS_HACKATHONS)
    )
    chunk = EvidenceChunk(
        SOURCE_GITHUB_COMMIT,
        "etl@c1",
        "Analysts reconciled export spreadsheets by hand for days\nReworked the exporter in Python",
    )
    draft = DraftClaim(
        action_text="Reworked the exporter in Python",
        action_tools=("Python",),
        problem_text="Analysts reconciled export spreadsheets by hand for days",
        problem_cost_dimension=None,
        problem_inefficiency=None,
        result_text="Cut export errors by 99%",  # 99 appears in NO cited chunk
        result_kind=ResultKind.QUANTIFIED,
        result_metric_json={"resolves": "quality", "metric_text": "99%"},
        evidence=(
            ClaimEvidenceRef(chunk=chunk, field=ClaimField.PROBLEM),
            ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),
            ClaimEvidenceRef(
                chunk=chunk, field=ClaimField.RESULT, outcome_quote="Cut export errors by 99%"
            ),
        ),
    )
    [claim] = repo.replace_unreviewed_claims(
        "u1",
        experience.id,
        [
            StorableClaim(
                draft=draft,
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.VERIFIED,
            )
        ],
    )
    # Hand-build a story that cites the claim (bypassing synthesis, which would quarantine it).
    content = StoryContent(
        problem_text="Analysts reconciled export spreadsheets by hand for days",
        problem_refs=(claim.id,),
        actions=(select_story_content(experience.id, [claim]).actions[0],),
        results=(select_story_content(experience.id, [claim]).results[0],),
    )
    story = stories.upsert_draft("u1", experience.id, content)
    stories.transition_story(story.id, StoryReviewStatus.PENDING_REVIEW)
    stories.transition_story(story.id, StoryReviewStatus.APPROVED)

    snapshot = create_story_snapshot("u1", stories, repo, InMemorySnapshotStore())
    assert all(section == [] for section in snapshot.content["sections"].values())
