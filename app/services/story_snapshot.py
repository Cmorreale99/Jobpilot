"""Story snapshot service: resolve approved stories into a renderable Master CV version.

This is the I/O half of Phase 4 (``docs/ARCHITECTURE_V3.md`` §7). ``create_story_snapshot``
is the story-shaped analog of ``create_master_cv_snapshot``: it reads the user's **approved**
project stories, resolves each story's components (evidenced or user-attested) and their
provenance out of the claim/evidence repository, number-grounds every derived bullet against
its cited evidence, and hands the resulting :class:`RenderableStory` values to the pure
domain builder (``domain/story_snapshot.py``), which applies the render-time resume-ready gate
and the duplicate-metric refusal before assembling the versioned ``content_json``.

Only ``approved`` stories on ``confirmed`` entities are considered — a merged/discarded
entity's story was invalidated back to ``draft`` (roster cascade) and cannot render. A story
whose bullets contain a number missing from its cited evidence is dropped and logged: the one
artifact that leaves the machine never states an ungrounded figure (T14).
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    ClaimRepository,
    Experience,
    ExperienceStatus,
    ResultKind,
)
from app.domain.master_cv_snapshot import MasterCvSnapshotStore, StoredSnapshot
from app.domain.project_story import (
    COMPONENT_PROBLEM,
    COMPONENT_RESULT,
    ProjectStory,
    ProjectStoryRepository,
    StoryReviewStatus,
    assess_problem_text,
    component_id,
    resolve_component_evidence,
    unsupported_number_tokens,
)
from app.domain.story_snapshot import (
    RenderableStory,
    build_story_snapshot_content,
    derive_bullets,
)

logger = logging.getLogger(__name__)


def _attestation(
    claim_repository: ClaimRepository, story: ProjectStory, component: str
) -> str | None:
    stored = claim_repository.get_evidence_by_ref(
        story.user_id, SOURCE_USER_ATTESTATION, f"story:{story.id}:{component}"
    )
    return stored.chunk_text if stored is not None else None


def _prepare_renderable(
    story: ProjectStory, experience: Experience, claim_repository: ClaimRepository
) -> RenderableStory | None:
    """Resolve one approved story into render-ready components, or ``None`` if it must drop.

    Mirrors ``compute_readiness``'s component signals so the render gate agrees with the
    review gate: a Problem is evidenced (composed text clearing the §3.1 bar with valid
    refs) or user-attested; a Result is evidenced (a cited non-missing claim) or
    user-attested; Actions come from the story's claims. Every bullet's numbers must appear
    in its cited evidence (or the attestation it cites) — a story that fails is dropped.
    """
    content = story.content
    claims_by_id = {
        c.id: c
        for c in claim_repository.list_claims(story.user_id)
        if c.experience_id == story.experience_id
    }

    # Problem: evidenced (composed, clears the bar, valid refs) or user-attested.
    attested_problem = _attestation(claim_repository, story, COMPONENT_PROBLEM)
    problem: dict[str, Any] | None = None
    valid_problem_refs = [i for i in content.problem_refs if i in claims_by_id]
    if (
        (content.problem_text or "").strip()
        and not assess_problem_text(content.problem_text or "")
        and valid_problem_refs
    ):
        problem = {"text": content.problem_text, "claim_ids": list(valid_problem_refs)}
    elif attested_problem:
        problem = {
            "text": attested_problem,
            "claim_ids": [],
            "attested": True,
            "source_ref": f"story:{story.id}:{COMPONENT_PROBLEM}",
        }

    # Actions: every selected action citing at least one of this entity's claims.
    actions: list[dict[str, Any]] = []
    for action in content.actions:
        valid_refs = [i for i in action.claim_ids if i in claims_by_id]
        if action.summary.strip() and valid_refs:
            actions.append(
                {
                    "component_id": action.component_id,
                    "text": action.summary,
                    "claim_ids": valid_refs,
                }
            )

    # Results: evidenced components first; fall back to a user-attested Result.
    results: list[dict[str, Any]] = []
    for result in content.results:
        valid = [claims_by_id[i] for i in result.claim_ids if i in claims_by_id]
        if any(c.result_kind is not ResultKind.MISSING for c in valid):
            results.append(
                {
                    "component_id": result.component_id,
                    "text": result.text,
                    "claim_ids": [c.id for c in valid],
                    "outcome_quote": result.outcome_quote,
                }
            )
    if not results:
        attested_result = _attestation(claim_repository, story, COMPONENT_RESULT)
        if attested_result:
            results.append(
                {
                    "component_id": component_id("r", attested_result),
                    "text": attested_result,
                    "claim_ids": [],
                    "outcome_quote": None,
                    "attested": True,
                    "source_ref": f"story:{story.id}:{COMPONENT_RESULT}",
                }
            )

    bullets = derive_bullets(actions, results)

    # Number gate (T14): every digit/number-word in a bullet must appear in the evidence
    # its component cites (or the attestation it is), never in other generated text.
    for bullet, component in zip(bullets, (*actions, *results), strict=True):
        allowed = [
            stored.chunk_text
            for stored in resolve_component_evidence(
                component["claim_ids"], claims_by_id, claim_repository.get_evidence
            )
        ]
        if component.get("attested"):
            allowed.append(component["text"])
        ungrounded = unsupported_number_tokens(bullet["text"], allowed)
        if ungrounded:
            logger.warning(
                "dropping approved story %s (experience %s) from the Master CV: bullet "
                "%r states ungrounded number(s) %s",
                story.id,
                story.experience_id,
                bullet["text"],
                ungrounded,
            )
            return None

    return RenderableStory(
        experience_id=story.experience_id,
        story_id=story.id,
        name=experience.name,
        section=experience.section.value,
        sort_order=experience.sort_order,
        subtitle=experience.subtitle or "",
        dates=experience.dates or "",
        problem=problem,
        actions=tuple(actions),
        results=tuple(results),
        bullets=tuple(bullets),
    )


def create_story_snapshot(
    user_id: str,
    story_repository: ProjectStoryRepository,
    claim_repository: ClaimRepository,
    store: MasterCvSnapshotStore,
) -> StoredSnapshot:
    """Snapshot the user's approved stories as a new (or unchanged) Master CV version.

    The version is story-shaped (``snapshot_of == "approved_stories"``); the store versions
    it above any legacy claim-shaped versions and returns the existing version unchanged when
    the content fingerprint matches (idempotent). Raises :class:`DuplicateMetricError` if two
    approved stories share an outcome span.
    """
    experiences = {e.id: e for e in claim_repository.list_experiences(user_id)}
    renderables: list[RenderableStory] = []
    for story in story_repository.list_stories(user_id, status=StoryReviewStatus.APPROVED):
        experience = experiences.get(story.experience_id)
        if experience is None or experience.status is not ExperienceStatus.CONFIRMED:
            continue
        renderable = _prepare_renderable(story, experience, claim_repository)
        if renderable is None:
            continue
        if not renderable.resume_ready:
            logger.warning(
                "dropping approved-but-incomplete story %s (experience %s) from the Master "
                "CV — the render-time resume-ready gate refuses it",
                story.id,
                story.experience_id,
            )
            continue
        renderables.append(renderable)

    content = build_story_snapshot_content(renderables)
    return store.save(user_id, content)
