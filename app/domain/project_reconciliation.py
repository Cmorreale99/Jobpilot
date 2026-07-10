"""Expected-project reconciliation: expected vs. detected, honestly (v3.1 Increment 5).

The audit's gap #6: the roster can only speak about what the sources produced — an
expected project that never made it out of parsing is indistinguishable from one whose
sources were never loaded. This module reconciles a user-stated **expected inventory**
against the confirmed roster and the raw gathered source texts, and names which of the
three worlds each expected project is in:

* ``detected`` — a confirmed roster entity matches by name/alias
  (:meth:`~app.domain.claims.Experience.matches_name`); the pipeline sees it.
* ``present_in_source_but_not_parsed`` — the name appears in a gathered source text but
  no confirmed entity matches: a **parsing/roster gap**, fixable inside the system
  (re-run detection, confirm the proposal, or assign the unassigned evidence).
* ``missing_from_resume_or_source_not_loaded`` — nothing anywhere: the fix is outside
  the system (``search_project_sources_or_ask_user_to_add``), and saying so is the
  honest answer — never a parsing failure by default.

Every expected project gets exactly one result, in input order — the reconciliation
never silently omits an entry (the whole point is surfacing absence).

Pure logic: no I/O, no repository — the caller supplies the roster and the texts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.claims import Experience

STATUS_DETECTED = "detected"
STATUS_PARSING_GAP = "present_in_source_but_not_parsed"
STATUS_MISSING = "missing_from_resume_or_source_not_loaded"

NEXT_ACTION_REVIEW_PARSING = "re_run_roster_detection_or_review_unassigned_evidence"
NEXT_ACTION_SEARCH_OR_ASK = "search_project_sources_or_ask_user_to_add"


@dataclass(frozen=True)
class ReconciliationResult:
    """Where one expected project stands, machine-readable."""

    expected_project: str
    detected_in_resume: bool
    status: str
    matched_experience_id: int | None = None
    matched_experience_name: str | None = None
    next_action: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """The spec JSON shape (what the validation log and any surface renders)."""
        payload: dict[str, Any] = {
            "expected_project": self.expected_project,
            "detected_in_resume": self.detected_in_resume,
            "status": self.status,
        }
        if self.matched_experience_id is not None:
            payload["matched_experience"] = {
                "id": self.matched_experience_id,
                "name": self.matched_experience_name,
            }
        if self.next_action is not None:
            payload["next_action"] = self.next_action
        return payload


def _name_variants(expected: str) -> tuple[str, ...]:
    """The forms an expected name may take in a roster or a source text.

    Inventories arrive as identifiers (``paper_recommender_system``) while rosters and
    prose carry display names ("Paper recommender system") or repo slugs
    (``paper-recommender-system``) — reconciliation must not miss on punctuation.
    """
    collapsed = " ".join(expected.split())
    spaced = " ".join(collapsed.replace("_", " ").replace("-", " ").split())
    hyphenated = spaced.replace(" ", "-")
    variants = {collapsed, spaced, hyphenated}
    return tuple(v for v in variants if v)


def _match_entity(expected: str, entities: Sequence[Experience]) -> Experience | None:
    for variant in _name_variants(expected):
        for entity in entities:
            if entity.matches_name(variant):
                return entity
    return None


def _mentioned_in_sources(expected: str, texts: Sequence[str]) -> bool:
    needles = tuple(v.casefold() for v in _name_variants(expected))
    for text in texts:
        haystack = " ".join(text.split()).casefold()
        if any(needle in haystack for needle in needles):
            return True
    return False


def reconcile_expected_projects(
    expected_inventory: Sequence[str],
    confirmed_entities: Sequence[Experience],
    raw_source_texts: Sequence[str],
) -> list[ReconciliationResult]:
    """One result per expected project, in input order — nothing silently omitted."""
    results: list[ReconciliationResult] = []
    for expected in expected_inventory:
        entity = _match_entity(expected, confirmed_entities)
        if entity is not None:
            results.append(
                ReconciliationResult(
                    expected_project=expected,
                    detected_in_resume=True,
                    status=STATUS_DETECTED,
                    matched_experience_id=entity.id,
                    matched_experience_name=entity.name,
                )
            )
        elif _mentioned_in_sources(expected, raw_source_texts):
            results.append(
                ReconciliationResult(
                    expected_project=expected,
                    detected_in_resume=False,
                    status=STATUS_PARSING_GAP,
                    next_action=NEXT_ACTION_REVIEW_PARSING,
                )
            )
        else:
            results.append(
                ReconciliationResult(
                    expected_project=expected,
                    detected_in_resume=False,
                    status=STATUS_MISSING,
                    next_action=NEXT_ACTION_SEARCH_OR_ASK,
                )
            )
    return results


__all__ = [
    "NEXT_ACTION_REVIEW_PARSING",
    "NEXT_ACTION_SEARCH_OR_ASK",
    "STATUS_DETECTED",
    "STATUS_MISSING",
    "STATUS_PARSING_GAP",
    "ReconciliationResult",
    "reconcile_expected_projects",
]
