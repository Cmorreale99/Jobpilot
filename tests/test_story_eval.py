"""Story-synthesis evaluation over the live-shaped corpus (V3 Phase 5b, §5).

The scorecard must surface the corpus's known pathologies — the cross-entity duplicate
outcome span (``duplicate_story_count``) and the cross-source metric conflict — while proving
the truthfulness invariants hold (no invented metrics, no orphan components, and — v3.1
Increment 6 — no cross-problem-space contamination: a mixed-space story is a hard
scorecard regression even though the structural validator cannot see it).
"""

from __future__ import annotations

from dataclasses import replace

from app.domain.project_story import StoryAction, component_id
from app.domain.validation_runs import KIND_STORY_EVAL
from app.services.claim_repository import InMemoryClaimRepository
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_eval import run_story_eval
from app.services.story_synthesis import run_story_synthesis
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.fixtures.problem_spaces.cooper_ai import seed_cooper_repository
from tests.live_corpus import build_live_corpus


def test_story_eval_scores_the_live_corpus() -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    run_story_synthesis(corpus.user_id, corpus.repository, stories)
    log = InMemoryValidationRunLog()

    report = run_story_eval(corpus.user_id, stories, corpus.repository, validation_log=log)

    assert report.total == 15  # one story per confirmed entity, claim-less ones included
    # Truthfulness invariants hold: the heuristic never invents a number or an orphan ref,
    # and per-space synthesis never blends problem spaces.
    assert report.invented_metric_count == 0
    assert report.orphan_component_count == 0
    assert report.cross_problem_space_contamination_count == 0
    # The planted OneWorld/portfolio "Top 3 out of 100+ teams" duplicate is surfaced —
    # so the run is NOT boundary_clean until the human merges it.
    assert report.duplicate_story_count >= 1
    assert report.boundary_clean is False
    # The MassDEP conflict pair surfaces as a cross-source conflict question.
    assert report.cross_source_conflict_count >= 1
    assert report.resume_ready >= 1

    runs = log.list_runs(corpus.user_id, KIND_STORY_EVAL)
    assert len(runs) == 1
    assert runs[0].passed is False  # boundary not clean → recorded as failing
    assert any("duplicate_story_count" in line for line in runs[0].detail)


def test_story_eval_is_clean_without_the_planted_duplicate() -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    # Synthesize every entity EXCEPT the portfolio container that shares OneWorld's quote.
    keep = {
        e.id
        for e in corpus.repository.list_experiences(corpus.user_id)
        if e.name != "cameron-morreale-portfolio"
    }
    run_story_synthesis(corpus.user_id, corpus.repository, stories, experience_ids=keep)

    report = run_story_eval(corpus.user_id, stories, corpus.repository)

    assert report.duplicate_story_count == 0
    assert report.invented_metric_count == 0
    assert report.orphan_component_count == 0
    assert report.cross_problem_space_contamination_count == 0
    assert report.boundary_clean is True


def test_a_mixed_space_story_is_a_hard_scorecard_regression() -> None:
    """v3.1 Increment 6: a FedEx story citing a Pacifica claim breaks ``boundary_clean``
    — precisely the blending the structural validator cannot see (same entity, valid
    refs, no ungrounded numbers)."""
    claims_repo = InMemoryClaimRepository()
    stories = InMemoryProjectStoryRepository()
    experience, claims = seed_cooper_repository(claims_repo)
    run_story_synthesis("u1", claims_repo, stories)

    clean = run_story_eval("u1", stories, claims_repo)
    assert clean.cross_problem_space_contamination_count == 0

    fedex = next(
        s
        for s in stories.list_stories_for_experience("u1", experience.id)
        if "FedEx" in (s.content.problem_text or "")
    )
    pacifica_claim = next(c for c in claims if "Automated recurring" in c.action_text)
    contaminated_action = StoryAction(
        component_id=component_id("a", pacifica_claim.action_text),
        summary=pacifica_claim.action_text,
        claim_ids=(pacifica_claim.id,),
    )
    stories.upsert_draft(
        "u1",
        experience.id,
        replace(fedex.content, actions=(*fedex.content.actions, contaminated_action)),
    )

    log = InMemoryValidationRunLog()
    report = run_story_eval("u1", stories, claims_repo, validation_log=log)

    assert report.cross_problem_space_contamination_count == 1
    # The other truthfulness metrics stay silent — only the new invariant sees this.
    assert report.invented_metric_count == 0
    assert report.orphan_component_count == 0
    assert report.boundary_clean is False
    [run] = log.list_runs("u1", KIND_STORY_EVAL)
    assert run.passed is False
    assert any("cross_problem_space_contamination_count=1" in line for line in run.detail)
