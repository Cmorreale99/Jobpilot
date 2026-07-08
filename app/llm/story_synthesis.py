"""LLM-backed story synthesis (DEEP tier) — composes the Problem, groups the Actions.

Implements the domain :class:`~app.domain.project_story.StorySynthesizer` protocol. Unlike
the heuristic (which selects claim text verbatim, §3.8), this engine is allowed to *author*
two things and only two:

* the **Problem Space** — 1–3 sentences composed from the claims' problem context, citing
  the claim ids that support it (§3.1);
* **strategic Action grouping** — related implementation details merged under 3–5 higher-level
  actions, each citing the claims it covers (§3.2).

**Results are never composed** — they carry invented-number risk, so they are selected
verbatim by the same heuristic path (``select_story_content``). Authoring is contained by
grounding, not trust: every number the model writes must already appear in the cited
evidence (``unsupported_number_tokens`` over the provenance walk), or that component is
dropped — an ungrounded Problem becomes ``needs-problem``, an ungrounded Action is discarded.
A composed Problem that fails the §3.1 structural bar (``assess_problem_text``) is likewise
dropped. Whatever survives still faces ``validate_story_structure`` + the readiness gate in
the synthesis service, so composition can never bypass the truthfulness invariants.

On any LLM failure the engine raises :class:`StorySynthesisError` — the service skips the
entity loudly (logged + recorded); there is no silent mid-run degrade to the heuristic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.claims import Claim, StoredEvidence
from app.domain.project_story import (
    MAX_STORY_ACTIONS,
    StoryAction,
    StoryContent,
    StorySynthesisError,
    assess_problem_text,
    component_id,
    resolve_component_evidence,
    select_story_content,
    unsupported_number_tokens,
)
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger("app.llm.story_synthesis")

_SYSTEM = (
    "You compose ONE project's Master CV story from a numbered list of already-verified "
    "career claims for that single project. You do TWO things and nothing else:\n"
    "1) Compose the PROBLEM SPACE: 1-3 sentences explaining why the project mattered — the "
    "real user, business, operational, or technical problem it existed to solve. Higher-level "
    "than a one-line bug or task. Cite the ids of the claims whose Problem context supports "
    "it. If no claim states a genuine pain point, return null — NEVER invent one.\n"
    "2) Group the ACTIONS into 3-5 strategic actions: merge related implementation details "
    "under one higher-level action. Each grouped action cites the ids of the claims it "
    "covers, names the concrete tools, and summarizes the work.\n"
    "\n"
    "HARD RULES:\n"
    "- Use ONLY facts present in the cited claims. NEVER invent capabilities, tools, or "
    "NUMBERS. Every number you write MUST already appear in a cited claim.\n"
    "- Do NOT produce Results — results are selected separately and verbatim.\n"
    "- Every id in a 'claim_ids' array must be one of the provided claim ids.\n"
    "\n"
    'Reply with ONLY JSON: {"problem": {"text": string, "claim_ids": [int]} | null, '
    '"actions": [{"summary": string, "claim_ids": [int], "tools": [string]}]}. '
    "No markdown, no commentary."
)


@dataclass(frozen=True)
class _ComposedProblem:
    text: str
    claim_ids: tuple[int, ...]


@dataclass(frozen=True)
class _ComposedAction:
    summary: str
    claim_ids: tuple[int, ...]
    tools: tuple[str, ...]


class LlmStorySynthesizer:
    """DEEP-tier synthesizer that composes the Problem + groups Actions; loud failure."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.DEEP,
        max_tokens: int = 2048,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens

    def synthesize(
        self,
        experience_id: int,
        claims: Sequence[Claim],
        get_evidence: Callable[[int], StoredEvidence | None],
        *,
        experience_name: str = "",
    ) -> StoryContent:
        pool = [c for c in claims if c.experience_id == experience_id]
        # Results stay heuristic (verbatim, deduped) — only Problem + Actions are composed.
        heuristic = select_story_content(experience_id, claims)
        if not pool:
            return heuristic

        by_id = {c.id: c for c in pool}
        try:
            problem, actions = complete_json(
                self._client,
                system=_SYSTEM,
                cached_context=f"Claims for this project:\n{_render_claims(pool)}",
                messages=[LlmMessage.user(_prompt(experience_name))],
                tier=self._tier,
                validator=lambda payload: _parse(payload, valid_ids=frozenset(by_id)),
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.error("LLM story synthesis failed for experience %s: %s", experience_id, exc)
            raise StorySynthesisError(
                f"LLM story synthesis failed for experience {experience_id}: {exc}"
            ) from exc

        grounded_problem = self._ground_problem(problem, by_id, get_evidence)
        grounded_actions = self._ground_actions(actions, by_id, get_evidence)

        return StoryContent(
            problem_text=grounded_problem.text if grounded_problem else None,
            problem_refs=grounded_problem.claim_ids if grounded_problem else (),
            actions=tuple(
                StoryAction(
                    component_id=component_id("a", action.summary),
                    summary=action.summary,
                    claim_ids=action.claim_ids,
                    tools=action.tools,
                )
                for action in grounded_actions
            ),
            results=heuristic.results,
        )

    def _ground_problem(
        self,
        problem: _ComposedProblem | None,
        by_id: dict[int, Claim],
        get_evidence: Callable[[int], StoredEvidence | None],
    ) -> _ComposedProblem | None:
        if problem is None:
            return None
        if assess_problem_text(problem.text):
            logger.warning("dropping composed Problem that fails the specificity bar")
            return None
        if _ungrounded_numbers(problem.text, problem.claim_ids, by_id, get_evidence):
            logger.warning("dropping composed Problem with an ungrounded number")
            return None
        return problem

    def _ground_actions(
        self,
        actions: Sequence[_ComposedAction],
        by_id: dict[int, Claim],
        get_evidence: Callable[[int], StoredEvidence | None],
    ) -> list[_ComposedAction]:
        grounded: list[_ComposedAction] = []
        for action in actions:
            if not action.summary.strip() or not action.claim_ids:
                continue
            if _ungrounded_numbers(action.summary, action.claim_ids, by_id, get_evidence):
                logger.warning(
                    "dropping composed Action with an ungrounded number: %r", action.summary
                )
                continue
            grounded.append(action)
            if len(grounded) >= MAX_STORY_ACTIONS:
                break
        return grounded


def _ungrounded_numbers(
    text: str,
    claim_ids: Sequence[int],
    by_id: dict[int, Claim],
    get_evidence: Callable[[int], StoredEvidence | None],
) -> list[str]:
    evidence_texts = [
        stored.chunk_text for stored in resolve_component_evidence(claim_ids, by_id, get_evidence)
    ]
    return unsupported_number_tokens(text, evidence_texts)


def _render_claims(claims: Sequence[Claim]) -> str:
    lines = []
    for claim in claims:
        lines.append(
            f"[claim {claim.id}] "
            f"problem={claim.problem_text or '(none stated)'} | "
            f"action={claim.action_text} | "
            f"result={claim.result_text or '(none)'} | "
            f"tools={list(claim.action_tools)}"
        )
    return "\n".join(lines)


def _prompt(experience_name: str) -> str:
    name = experience_name.strip() or "(unnamed project)"
    return (
        f"Project: {name}\n"
        "The verified claims are provided in the system context above.\n"
        "Compose the Problem Space and group the Actions per the rules."
    )


def _parse(
    payload: Any, *, valid_ids: frozenset[int]
) -> tuple[_ComposedProblem | None, list[_ComposedAction]]:
    """Validate the JSON and keep only ids from the provided claim list (raise to retry)."""
    if not isinstance(payload, dict):
        raise ValueError("expected an object")

    problem: _ComposedProblem | None = None
    raw_problem = payload.get("problem")
    if isinstance(raw_problem, dict):
        text = raw_problem.get("text")
        ids = _valid_ids(raw_problem.get("claim_ids"), valid_ids)
        if isinstance(text, str) and text.strip() and ids:
            problem = _ComposedProblem(text=text.strip(), claim_ids=ids)

    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list):
        raise ValueError("'actions' must be an array")
    actions: list[_ComposedAction] = []
    for entry in raw_actions:
        if not isinstance(entry, dict):
            raise ValueError("each action must be an object")
        summary = entry.get("summary")
        ids = _valid_ids(entry.get("claim_ids"), valid_ids)
        if not isinstance(summary, str) or not summary.strip() or not ids:
            continue  # an action with no valid claim refs is dropped, not fatal
        tools_raw = entry.get("tools", [])
        tools = (
            tuple(str(t).strip() for t in tools_raw if isinstance(t, str) and t.strip())
            if isinstance(tools_raw, list)
            else ()
        )
        actions.append(_ComposedAction(summary=summary.strip(), claim_ids=ids, tools=tools))
    return problem, actions


def _valid_ids(raw: Any, valid_ids: frozenset[int]) -> tuple[int, ...]:
    if not isinstance(raw, list):
        return ()
    seen: dict[int, None] = {}
    for item in raw:
        if isinstance(item, int) and item in valid_ids and item not in seen:
            seen[item] = None
    return tuple(seen)
