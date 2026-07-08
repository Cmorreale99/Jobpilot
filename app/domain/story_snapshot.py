"""Story-shaped Master CV snapshot: render from APPROVED project stories (V3 Phase 4).

V2's Master CV version was a snapshot of approved *claims* (``domain/master_cv_snapshot.py``).
V3's render unit is the **story** (``docs/ARCHITECTURE_V3.md`` §3.4, §6, §7 Phase 4): the
Master CV version is an immutable snapshot of the user's **approved** project stories, and
nothing else downstream is allowed to see raw claims. This module is the pure core of that
snapshot — no I/O, no persistence:

* **The render-time resume-ready gate** (§3.4, third enforcement) — a story renders only
  when it is structurally complete: a Problem, ≥1 Action, and a Result. This is the same
  bar the domain gate (``approval_blockers``) and the API 409 enforce, restated where the
  bytes are written: a *force-approved* incomplete story (one that bypassed the API) still
  never reaches the docx (T12). The builder reads the resolved components, so "approved but
  incomplete" is dropped, not rendered.
* **Duplicate-metric refusal** (§3.7, final-CV enforcement — enforced nowhere before V3):
  the same outcome span may back at most one story across the whole CV. Two approved
  stories citing the same result (the live "Top 3 out of 100+ teams" under both ``OneWorld``
  and the portfolio entity) make the render **refuse the snapshot** (``DuplicateMetricError``),
  forcing a roster merge — never a silent pick of one (T10/T11).
* **Deterministic bullets** (§2.2) — the renderer derives bullets from the P/A/R components
  verbatim, exactly like V2's ``render_claim_bullet``: no rewriting, no invention. Each
  bullet carries its ``component_id`` + ``claim_ids`` so bullet → component → claim →
  evidence stays traceable (T13).

The caller (``services/story_snapshot.py``) resolves each approved story's components and
attestations into :class:`RenderableStory` values (that is where the I/O lives); this module
applies the rules and assembles the versioned ``content_json``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.claims import ExperienceSection
from app.domain.cv import MasterCv, ParClaim
from app.domain.master_cv_snapshot import StoredSnapshot, master_cv_from_snapshot

# A story-shaped Master CV version, distinguished from V2's ``approved_claims`` snapshots so
# old claim-shaped versions stay readable (§6 — the store versions both kinds in one table).
STORY_SNAPSHOT_KIND = "approved_stories"

# ``ParClaim.source_type`` for components adapted from an approved-story snapshot (the
# Phase 5 downstream adapter); the provenance is the reviewed story component itself.
SOURCE_APPROVED_STORY = "approved_story"


class DuplicateMetricError(RuntimeError):
    """One outcome span backs two different stories — the render refuses the snapshot (§3.7).

    Duplication across stories is a relation resolved by a roster merge, never a silent
    "keep the strongest" pick. ``spans`` lists each shared span with the experience ids
    that both claim it.
    """

    def __init__(self, spans: Sequence[tuple[str, tuple[int, ...]]]) -> None:
        detail = "; ".join(
            f"{span!r} under experiences {list(experiences)}" for span, experiences in spans
        )
        super().__init__(
            "refusing to render: the same outcome span backs more than one story "
            f"(merge the duplicates first) — {detail}"
        )
        self.spans = tuple(spans)


def _normalize(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _as_sentence(text: str) -> str:
    stripped = text.strip()
    return stripped if stripped.endswith((".", "!", "?")) else stripped + "."


def derive_bullets(
    actions: Sequence[dict[str, Any]], results: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deterministic P/A/R → resume bullets: each Action, then each Result, verbatim.

    The Problem gates the story but is not itself a bullet (V2 never bulleted it either —
    a resume bullet is an accomplishment, the Problem is why the project mattered). Every
    bullet keeps its ``component_id`` and ``claim_ids`` so the render stays traceable (T13).
    """
    bullets: list[dict[str, Any]] = []
    for component in (*actions, *results):
        bullets.append(
            {
                "text": _as_sentence(component["text"]),
                "component_id": component["component_id"],
                "claim_ids": list(component.get("claim_ids", ())),
            }
        )
    return bullets


@dataclass(frozen=True)
class RenderableStory:
    """One approved story resolved into render-ready components (all data, no I/O).

    ``problem`` is ``None`` when the story has neither an evidenced nor an attested
    Problem; ``results`` is empty when it has neither an evidenced nor an attested Result.
    Those absences are exactly what the render gate keys on: :attr:`resume_ready` is the
    third enforcement of the resume-ready bar (§3.4).
    """

    experience_id: int
    story_id: int
    name: str
    section: str
    sort_order: int
    subtitle: str = ""
    dates: str = ""
    problem: dict[str, Any] | None = None
    actions: tuple[dict[str, Any], ...] = ()
    results: tuple[dict[str, Any], ...] = ()
    bullets: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def resume_ready(self) -> bool:
        """The render-time P-A-R bar: a Problem, ≥1 Action, and a Result (§3.4)."""
        return self.problem is not None and bool(self.actions) and bool(self.results)


def _story_entry(story: RenderableStory) -> dict[str, Any]:
    return {
        "experience_id": story.experience_id,
        "story_id": story.story_id,
        "name": story.name,
        "subtitle": story.subtitle,
        "dates": story.dates,
        "sort_order": story.sort_order,
        "problem": story.problem,
        "actions": [dict(action) for action in story.actions],
        "results": [dict(result) for result in story.results],
        "bullets": [dict(bullet) for bullet in story.bullets],
    }


def _duplicate_spans(
    stories: Sequence[RenderableStory],
) -> list[tuple[str, tuple[int, ...]]]:
    """Outcome spans that back a Result under more than one story (§3.7)."""
    by_span: dict[str, set[int]] = {}
    for story in stories:
        for result in story.results:
            span = _normalize(result.get("outcome_quote") or result.get("text"))
            if span:
                by_span.setdefault(span, set()).add(story.experience_id)
    return sorted(
        (span, tuple(sorted(experiences)))
        for span, experiences in by_span.items()
        if len(experiences) > 1
    )


def build_story_snapshot_content(renderables: Sequence[RenderableStory]) -> dict[str, Any]:
    """Build a Master CV version's ``content_json`` from approved stories.

    Only :attr:`RenderableStory.resume_ready` stories render — the render-time gate (§3.4):
    a story a human somehow approved while incomplete is dropped here, never emitted. The
    surviving stories are checked for cross-story duplicate outcome spans (§3.7) — a
    duplicate raises :class:`DuplicateMetricError` rather than double-counting one
    achievement across two resume projects. Entries group by the user-assigned ``section``,
    ordered by ``sort_order`` then id, matching the claim-shaped snapshot's layout.
    """
    ready = [story for story in renderables if story.resume_ready]

    duplicates = _duplicate_spans(ready)
    if duplicates:
        raise DuplicateMetricError(duplicates)

    sections: dict[str, list[dict[str, Any]]] = {
        ExperienceSection.PROFESSIONAL_EXPERIENCE.value: [],
        ExperienceSection.PROJECTS_HACKATHONS.value: [],
    }
    for story in sorted(ready, key=lambda s: (s.sort_order, s.experience_id)):
        sections[story.section].append(_story_entry(story))
    return {"snapshot_of": STORY_SNAPSHOT_KIND, "sections": sections}


def story_master_cv_from_snapshot(snapshot: StoredSnapshot) -> MasterCv:
    """Adapt an approved-**story** snapshot into the :class:`MasterCv` consumers expect (§6).

    The V3 story snapshot is the only input to matching, tailoring, outreach, and interview
    prep — this is the single bridge, mirroring ``master_cv_from_snapshot`` for claims. Each
    story component becomes one :class:`ParClaim` addressed by its **stable component id**
    (``source_ref = story:<story_id>:<component_id>``), so a downstream highlight reference
    is component-precise and cannot dangle when an *unapproved* draft is re-synthesized.

    Per story (always resume-ready, so ≥1 Action, a Result, and a Problem):

    * each **Action** → an action-only ``ParClaim`` (a capability line);
    * each **Result** → a ``ParClaim`` pairing the story's lead Action with the Result (an
      impact line, rendered "action — result" by the tailorer).

    ``evidence_text`` carries the component's **cited evidence** (not its possibly-composed
    text): the tailoring/outreach number gate grounds against it, so generated prose can
    never be grounded by other generated prose.
    """
    claims: list[ParClaim] = []
    sections: dict[str, Any] = snapshot.content.get("sections", {})
    for entries in sections.values():
        for entry in entries:
            story_id = entry.get("story_id")
            problem = entry.get("problem") or {}
            problem_text = problem.get("text") or None
            actions = entry.get("actions", [])
            results = entry.get("results", [])
            if not actions:
                continue  # a resume-ready story always has one; belt for a malformed row
            lead = actions[0]

            for action in actions:
                claims.append(
                    ParClaim(
                        action=action["text"],
                        problem=problem_text,
                        result=None,
                        source_type=SOURCE_APPROVED_STORY,
                        source_ref=f"story:{story_id}:{action['component_id']}",
                        evidence_text=action.get("evidence_text", ""),
                    )
                )
            for result in results:
                evidence = "\n".join(
                    text
                    for text in (lead.get("evidence_text", ""), result.get("evidence_text", ""))
                    if text
                )
                claims.append(
                    ParClaim(
                        action=lead["text"],
                        problem=problem_text,
                        result=result["text"],
                        source_type=SOURCE_APPROVED_STORY,
                        source_ref=f"story:{story_id}:{result['component_id']}",
                        evidence_text=evidence,
                    )
                )
    return MasterCv(claims=claims, sources=[], version=snapshot.version)


def master_cv_from_any_snapshot(snapshot: StoredSnapshot) -> MasterCv:
    """Adapt a stored Master CV version regardless of its kind (§6 snapshot versioning).

    Story-shaped versions (``approved_stories``) use the story adapter; older claim-shaped
    versions (``approved_claims``) still read through the claim adapter, so a consumer that
    reads ``get_latest`` — interview prep does — keeps working across the lineage restart.
    """
    if snapshot.content.get("snapshot_of") == STORY_SNAPSHOT_KIND:
        return story_master_cv_from_snapshot(snapshot)
    return master_cv_from_snapshot(snapshot)  # claim-shaped (or unlabelled legacy)


__all__ = [
    "SOURCE_APPROVED_STORY",
    "STORY_SNAPSHOT_KIND",
    "DuplicateMetricError",
    "RenderableStory",
    "build_story_snapshot_content",
    "derive_bullets",
    "master_cv_from_any_snapshot",
    "story_master_cv_from_snapshot",
]
