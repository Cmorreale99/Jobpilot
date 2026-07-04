"""LLM-backed two-pass claim extractor (BULK tier).

Implements the domain :class:`~app.domain.claims.ClaimExtractor` protocol as two
Messages calls over the same evidence chunks, mirroring the extraction philosophy:

* **Pass 1** finds WORK statements -> Actions (with the Problem context, when the
  evidence states one). Work statements can never become Results.
* **Pass 2** hunts specifically for OUTCOME statements -> Results, quoting them
  verbatim and tagging which declared pain point each resolves.

Prompting alone guarantees nothing, so every quote the model returns is grounded
against the actual chunk text (:func:`~app.domain.par_validation.verbatim_in` — the
same check the PAR validator runs) and ungrounded output is dropped: an ungrounded
action drops the claim, an ungrounded result leaves ``result_kind=missing`` rather
than ever inventing an outcome. The PAR validator downstream is the final gate.

On a bounced re-extraction the validator's specific violations are folded into both
prompts. On any LLM failure the extractor raises :class:`ClaimExtractionError` — the
service skips the group loudly (logged + recorded in ``validation_runs``) and existing
claims stay untouched. There is deliberately NO silent heuristic fallback: swapping in
a much dumber extractor mid-run with no operator signal is how mixed-quality claims
ended up coexisting in the same run (docs/V2_AUDIT.md).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.claims import (
    ClaimEvidenceRef,
    ClaimExtractionError,
    ClaimField,
    CostDimension,
    DraftClaim,
    EvidenceGroup,
    Inefficiency,
    ResultKind,
)
from app.domain.par_validation import verbatim_in
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger("app.llm.extraction")

_PASS1_SYSTEM = (
    "You extract career claims from evidence chunks (commit messages, READMEs, project "
    "docs). This is PASS 1 of 2: find WORK STATEMENTS - things the person did, naming "
    "concrete tools/tech - and the PROBLEM context when the evidence states one.\n"
    "\n"
    "Definitions (non-negotiable):\n"
    "- A Problem is a pain point costing business value. It must declare a "
    "cost_dimension (money|time|risk|quality|revenue), an inefficiency "
    "(manual|slow|not_working|integration_difficulty|not_user_friendly), or both. "
    "A task description is NOT a problem; use null when the evidence states no pain point.\n"
    "- An Action is what the person did, naming concrete tools/tech.\n"
    "- Do NOT extract results in this pass.\n"
    "\n"
    "Hard rules:\n"
    "1. Use ONLY information present in the chunks. Never invent.\n"
    "2. 'action_quote' and 'problem_quote' MUST be exact, contiguous, verbatim "
    "substrings of the chunk they cite (by chunk_index).\n"
    "3. One claim per distinct piece of work.\n"
    "\n"
    'Reply with ONLY JSON: {"claims": [{"chunk_index": int, "action": string, '
    '"tools": [string], "action_quote": string, "problem": {"text": string, '
    '"quote": string, "chunk_index": int, "cost_dimension": string|null, '
    '"inefficiency": string|null} | null}]}. No markdown, no commentary.'
)

_PASS2_SYSTEM = (
    "This is PASS 2 of 2: hunt the same evidence chunks specifically for OUTCOME "
    "STATEMENTS and attach them to the claims from pass 1 as Results.\n"
    "\n"
    "An OUTCOME statement expresses realized business impact ('cut failure rate from "
    "12% to 0.5%', 'ops no longer reconciles manually'). A WORK statement ('fixed "
    "dedup key mismatch') is NOT an outcome and must never be used as a Result. "
    "Any source may hold an outcome - commits and comments included; judge the "
    "content, not the source.\n"
    "\n"
    "Hard rules:\n"
    "1. 'outcome_quote' MUST be an exact, contiguous, verbatim substring of the cited "
    "chunk. 'metric_text' (quantified only) must also appear verbatim in that chunk.\n"
    "2. result_kind is 'quantified' (a number/metric) or 'qualitative_evidenced' "
    "(evidenced state change without a number).\n"
    "3. 'resolves' names the declared cost_dimension or inefficiency of the claim's "
    "Problem that the outcome addresses. Use null when nothing genuinely matches - "
    "never force a coupling.\n"
    "4. If a claim has no outcome statement anywhere in the evidence, OMIT it: a "
    "missing Result is recorded as missing, never invented.\n"
    "\n"
    'Reply with ONLY JSON: {"results": [{"claim_index": int, "chunk_index": int, '
    '"outcome_quote": string, "result_text": string, "result_kind": string, '
    '"metric_text": string|null, "resolves": string|null}]}. No markdown.'
)


@dataclass(frozen=True)
class _Pass1Claim:
    chunk_index: int
    action: str
    tools: tuple[str, ...]
    action_quote: str
    problem_text: str | None
    problem_quote: str | None
    problem_chunk_index: int | None
    cost_dimension: CostDimension | None
    inefficiency: Inefficiency | None


@dataclass(frozen=True)
class _Pass2Result:
    claim_index: int
    chunk_index: int
    outcome_quote: str
    result_text: str
    result_kind: ResultKind
    metric_text: str | None
    resolves: str | None


class LlmTwoPassExtractor:
    """Two-pass PAR extraction via an :class:`LlmClient`; loud failure, no fallback."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.BULK,
        max_tokens: int = 8192,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens

    def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
        if not any(chunk.chunk_text.strip() for chunk in group.chunks):
            return []
        try:
            pass1 = complete_json(
                self._client,
                system=_PASS1_SYSTEM,
                messages=[LlmMessage.user(_pass1_prompt(group, violations))],
                tier=self._tier,
                validator=_parse_pass1,
                max_tokens=self._max_tokens,
            )
            grounded = self._ground_pass1(group, pass1)
            if not grounded:
                return []
            pass2 = complete_json(
                self._client,
                system=_PASS2_SYSTEM,
                messages=[LlmMessage.user(_pass2_prompt(group, grounded, violations))],
                tier=self._tier,
                validator=_parse_pass2,
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.error("LLM extraction failed for %r: %s", group.experience.name, exc)
            raise ClaimExtractionError(
                f"LLM extraction failed for {group.experience.name!r}: {exc}"
            ) from exc
        return self._assemble(group, grounded, pass2)

    def _ground_pass1(self, group: EvidenceGroup, claims: list[_Pass1Claim]) -> list[_Pass1Claim]:
        grounded: list[_Pass1Claim] = []
        for claim in claims:
            if not _quote_ok(group, claim.chunk_index, claim.action_quote):
                logger.warning("dropping claim with ungrounded action quote: %r", claim.action)
                continue
            if claim.problem_quote is not None and not _quote_ok(
                group, claim.problem_chunk_index, claim.problem_quote
            ):
                logger.warning("dropping ungrounded problem from claim: %r", claim.action)
                claim = _Pass1Claim(
                    chunk_index=claim.chunk_index,
                    action=claim.action,
                    tools=claim.tools,
                    action_quote=claim.action_quote,
                    problem_text=None,
                    problem_quote=None,
                    problem_chunk_index=None,
                    cost_dimension=None,
                    inefficiency=None,
                )
            grounded.append(claim)
        return grounded

    def _assemble(
        self,
        group: EvidenceGroup,
        claims: list[_Pass1Claim],
        results: list[_Pass2Result],
    ) -> list[DraftClaim]:
        result_by_claim: dict[int, _Pass2Result] = {}
        for result in results:
            if result.claim_index not in range(len(claims)):
                continue
            if not _quote_ok(group, result.chunk_index, result.outcome_quote):
                logger.warning("dropping ungrounded outcome quote; Result stays missing")
                continue
            if result.result_kind is ResultKind.QUANTIFIED and (
                result.metric_text is None
                or not _quote_ok(group, result.chunk_index, result.metric_text)
            ):
                logger.warning("dropping quantified result with ungrounded metric")
                continue
            result_by_claim.setdefault(result.claim_index, result)

        drafts: list[DraftClaim] = []
        for index, claim in enumerate(claims):
            evidence = [
                ClaimEvidenceRef(chunk=group.chunks[claim.chunk_index], field=ClaimField.ACTION)
            ]
            if claim.problem_text is not None and claim.problem_chunk_index is not None:
                evidence.append(
                    ClaimEvidenceRef(
                        chunk=group.chunks[claim.problem_chunk_index],
                        field=ClaimField.PROBLEM,
                    )
                )
            attached = result_by_claim.get(index)
            result_text: str | None = None
            result_kind = ResultKind.MISSING
            result_metric_json: dict[str, Any] | None = None
            if attached is not None:
                result_text = attached.result_text
                result_kind = attached.result_kind
                result_metric_json = {"resolves": attached.resolves}
                if attached.result_kind is ResultKind.QUANTIFIED:
                    result_metric_json["metric_text"] = attached.metric_text
                evidence.append(
                    ClaimEvidenceRef(
                        chunk=group.chunks[attached.chunk_index],
                        field=ClaimField.RESULT,
                        outcome_quote=attached.outcome_quote,
                    )
                )
            drafts.append(
                DraftClaim(
                    action_text=claim.action,
                    action_tools=claim.tools,
                    problem_text=claim.problem_text,
                    problem_cost_dimension=claim.cost_dimension,
                    problem_inefficiency=claim.inefficiency,
                    result_text=result_text,
                    result_kind=result_kind,
                    result_metric_json=result_metric_json,
                    evidence=tuple(evidence),
                )
            )
        return drafts


def _quote_ok(group: EvidenceGroup, chunk_index: int | None, quote: str) -> bool:
    if chunk_index is None or chunk_index not in range(len(group.chunks)):
        return False
    return verbatim_in(quote, group.chunks[chunk_index].chunk_text)


def _render_chunks(group: EvidenceGroup) -> str:
    parts = []
    for index, chunk in enumerate(group.chunks):
        parts.append(
            f"[chunk {index}] source_type={chunk.source_type} source_ref={chunk.source_ref}\n"
            f"{chunk.chunk_text}"
        )
    return "\n---\n".join(parts)


def _violations_note(violations: Sequence[str]) -> str:
    if not violations:
        return ""
    joined = "\n".join(f"- {violation}" for violation in violations)
    return (
        "\n\nA previous extraction of this evidence FAILED validation with the "
        f"following specific violations; correct them this time:\n{joined}"
    )


def _pass1_prompt(group: EvidenceGroup, violations: Sequence[str]) -> str:
    return (
        f"Experience: {group.experience.name}\n"
        "Evidence chunks:\n"
        f"{_render_chunks(group)}\n"
        f"{_violations_note(violations)}\n"
        "Extract the pass-1 claims (work statements + problem context) per the rules."
    )


def _pass2_prompt(
    group: EvidenceGroup, claims: list[_Pass1Claim], violations: Sequence[str]
) -> str:
    claim_lines = "\n".join(
        f"[claim {index}] problem={claim.problem_text or '(none declared)'} | "
        f"declared={[v.value for v in (claim.cost_dimension, claim.inefficiency) if v]} | "
        f"action={claim.action}"
        for index, claim in enumerate(claims)
    )
    return (
        f"Experience: {group.experience.name}\n"
        "Evidence chunks:\n"
        f"{_render_chunks(group)}\n\n"
        "Pass-1 claims:\n"
        f"{claim_lines}\n"
        f"{_violations_note(violations)}\n"
        "Hunt for outcome statements and attach Results per the rules."
    )


def _parse_pass1(payload: Any) -> list[_Pass1Claim]:
    """Validate the pass-1 JSON; raise to trigger the JSON retry."""
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        raise ValueError("expected an object with a 'claims' array")
    claims: list[_Pass1Claim] = []
    for entry in payload["claims"]:
        if not isinstance(entry, dict):
            raise ValueError("each claim must be an object")
        action = entry.get("action")
        quote = entry.get("action_quote")
        chunk_index = entry.get("chunk_index")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("each claim needs a non-empty 'action'")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError("each claim needs a non-empty 'action_quote'")
        if not isinstance(chunk_index, int):
            raise ValueError("each claim needs an integer 'chunk_index'")
        tools_raw = entry.get("tools", [])
        tools = (
            tuple(str(tool).strip() for tool in tools_raw if isinstance(tool, str) and tool.strip())
            if isinstance(tools_raw, list)
            else ()
        )
        problem = entry.get("problem")
        problem_text = problem_quote = None
        problem_chunk_index = None
        cost = ineff = None
        if isinstance(problem, dict):
            text = problem.get("text")
            pquote = problem.get("quote")
            pindex = problem.get("chunk_index")
            if isinstance(text, str) and text.strip() and isinstance(pquote, str):
                problem_text = text.strip()
                problem_quote = pquote
                problem_chunk_index = pindex if isinstance(pindex, int) else None
                cost = _coerce_enum(CostDimension, problem.get("cost_dimension"))
                ineff = _coerce_enum(Inefficiency, problem.get("inefficiency"))
        claims.append(
            _Pass1Claim(
                chunk_index=chunk_index,
                action=action.strip(),
                tools=tools,
                action_quote=quote,
                problem_text=problem_text,
                problem_quote=problem_quote,
                problem_chunk_index=problem_chunk_index,
                cost_dimension=cost,
                inefficiency=ineff,
            )
        )
    return claims


def _parse_pass2(payload: Any) -> list[_Pass2Result]:
    """Validate the pass-2 JSON; raise to trigger the JSON retry."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("expected an object with a 'results' array")
    results: list[_Pass2Result] = []
    for entry in payload["results"]:
        if not isinstance(entry, dict):
            raise ValueError("each result must be an object")
        claim_index = entry.get("claim_index")
        chunk_index = entry.get("chunk_index")
        quote = entry.get("outcome_quote")
        text = entry.get("result_text")
        kind_raw = entry.get("result_kind")
        if not isinstance(claim_index, int) or not isinstance(chunk_index, int):
            raise ValueError("each result needs integer 'claim_index' and 'chunk_index'")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError("each result needs a non-empty 'outcome_quote'")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("each result needs a non-empty 'result_text'")
        kind = _coerce_enum(ResultKind, kind_raw)
        if kind not in (ResultKind.QUANTIFIED, ResultKind.QUALITATIVE_EVIDENCED):
            raise ValueError("result_kind must be quantified or qualitative_evidenced")
        metric = entry.get("metric_text")
        resolves = entry.get("resolves")
        results.append(
            _Pass2Result(
                claim_index=claim_index,
                chunk_index=chunk_index,
                outcome_quote=quote,
                result_text=text.strip(),
                result_kind=kind,
                metric_text=metric if isinstance(metric, str) and metric.strip() else None,
                resolves=resolves if isinstance(resolves, str) and resolves.strip() else None,
            )
        )
    return results


def _coerce_enum[E: StrEnum](enum_cls: type[E], value: Any) -> E | None:
    """Coerce a model-supplied string to an enum member, treating junk as absent."""
    if not isinstance(value, str):
        return None
    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        return None
