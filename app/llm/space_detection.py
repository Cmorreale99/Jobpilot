"""LLM-backed problem-space grouping (DEEP tier) — v3.1 Increment 7.

Implements the domain :class:`~app.domain.problem_space.ProblemSpaceDetector`
protocol. The model does ONE thing: partition an entity's distinct bar-clearing
problem statements into problem spaces — the semantic judgment lexical relatedness
can't make (the live corpus's six carbon-market facets are one narrative; five
rewordings of "heterogeneous rate-limited APIs" are one problem). It groups by
**index**, never by text: it cannot rewrite, merge, or invent a problem statement,
and the domain sanitizer (``_sanitized_groups``) re-grounds the output regardless —
unknown entries dropped, omitted problems restored as singleton spaces, first
membership wins. Everything downstream of grouping (attachment, candidates, ids,
statuses, the bundle validators) is the same deterministic code the heuristic uses.

Cost shape: :func:`~app.domain.problem_space.detect_problem_spaces` consults a
detector only when an entity has ≥2 distinct problems, so a synthesis run spends at
most one small DEEP call per multi-problem entity (the prompt is the numbered
problem sentences, nothing else).

On any LLM failure the detector raises
:class:`~app.domain.problem_space.ProblemSpaceDetectionError` — the synthesis
service skips the entity loudly (logged + recorded in ``validation_runs``); there is
no silent mid-run degrade to the heuristic.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.domain.problem_space import ProblemSpaceDetectionError
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger("app.llm.space_detection")

_SYSTEM = (
    "You group problem statements extracted from ONE project's sources into problem "
    "spaces. A problem space is one underlying challenge the project tackled. "
    "Statements describing the SAME challenge belong in ONE group — including "
    "rewordings of one problem from different sources, and different facets of one "
    "coherent problem narrative. Genuinely separate challenges or workstreams get "
    "separate groups.\n"
    "\n"
    "HARD RULES:\n"
    "- You only assign the given statement numbers to groups. NEVER rewrite, merge, "
    "or invent statement text.\n"
    "- Every statement number appears in exactly one group.\n"
    "- When unsure whether two statements share a challenge, keep them separate.\n"
    "\n"
    'Reply with ONLY JSON: {"groups": [[int, ...], ...]} using the statement '
    "numbers. No markdown, no commentary."
)


class LlmProblemSpaceDetector:
    """DEEP-tier grouping of problem statements by index; loud failure."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.DEEP,
        max_tokens: int = 512,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens

    def group_problems(self, problems: Sequence[str]) -> list[list[str]]:
        if len(problems) < 2:
            return [list(problems)] if problems else []
        numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(problems, start=1))
        try:
            index_groups = complete_json(
                self._client,
                system=_SYSTEM,
                messages=[
                    LlmMessage.user(
                        f"Problem statements for one project:\n{numbered}\n\n"
                        "Group them into problem spaces per the rules."
                    )
                ],
                tier=self._tier,
                validator=lambda payload: _parse(payload, count=len(problems)),
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.error("LLM problem-space grouping failed: %s", exc)
            raise ProblemSpaceDetectionError(f"LLM problem-space grouping failed: {exc}") from exc
        return [[problems[index - 1] for index in group] for group in index_groups]


def _parse(payload: Any, *, count: int) -> list[list[int]]:
    """Validate the JSON shape and keep only in-range, first-seen indices (raise to retry).

    Out-of-range or duplicate indices are dropped rather than fatal (the domain
    sanitizer restores anything omitted as a singleton space), but a malformed
    payload raises so ``complete_json`` re-asks with the corrective instruction.
    """
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        raise ValueError("'groups' must be an array of arrays")
    seen: set[int] = set()
    groups: list[list[int]] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list):
            raise ValueError("each group must be an array of statement numbers")
        indices: list[int] = []
        for item in raw_group:
            if isinstance(item, int) and 1 <= item <= count and item not in seen:
                seen.add(item)
                indices.append(item)
        if indices:
            groups.append(indices)
    if not groups:
        raise ValueError("no valid statement numbers in any group")
    return groups


__all__ = ["LlmProblemSpaceDetector"]
