"""LLM-backed PAR claim structurer.

Implements the domain :class:`~app.domain.cv.ClaimStructurer` protocol by asking the BULK
model to turn one source document into Problem-Action-Result claims — a drop-in for the
deterministic :class:`~app.domain.cv.HeuristicClaimStructurer`, injected the same way into
:class:`~app.domain.cv.MasterCvBuilder`. Because it lives in ``app/llm/``, ``domain/`` stays
free of any LLM import.

**No invented experience** is the load-bearing invariant, and prompting alone can't
guarantee it. So the model must return a verbatim ``evidence_text`` quote for every claim,
and :meth:`LlmClaimStructurer.structure` *drops any claim whose quote is not actually found
in the source* (whitespace-normalized). The prompt asks the model not to fabricate; the
grounding filter is the hard guarantee that survives a model that does anyway.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.domain.cv import CvSource, HeuristicClaimStructurer, ParClaim
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger("app.llm.claim_structurer")

_SYSTEM_PROMPT = (
    "You extract Problem-Action-Result (PAR) career claims from a single source document "
    "(a resume, project write-up, portfolio doc, or transcript). You are an extractor, "
    "not a writer.\n"
    "\n"
    "Hard rules:\n"
    "1. Use ONLY information present in the document. Never infer, embellish, or invent "
    "employers, metrics, dates, technologies, or outcomes that are not written there.\n"
    "2. For each claim, 'evidence_text' MUST be an exact, contiguous, verbatim substring "
    "copied from the document — the sentence(s) the claim is based on. Do not paraphrase "
    "the evidence.\n"
    "3. 'action' is what the person did (required). 'problem' (the situation/challenge) "
    "and 'result' (the measurable outcome) are optional: use null when the document does "
    "not state them. Do NOT fabricate a problem or result to fill the slot.\n"
    "4. Skip headers, contact details, and prose that is not an accomplishment.\n"
    "\n"
    'Reply with ONLY a JSON object of the form {"claims": [{"problem": string|null, '
    '"action": string, "result": string|null, "evidence_text": string}]}. '
    "No markdown, no commentary. If there are no accomplishments, return "
    '{"claims": []}.'
)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _ExtractedClaim:
    """A validated-but-not-yet-grounded claim as returned by the model."""

    action: str
    evidence_text: str
    problem: str | None
    result: str | None


class LlmClaimStructurer:
    """Extracts grounded PAR claims from a :class:`CvSource` via an :class:`LlmClient`."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.BULK,
        # Output scales with document size (every claim carries a verbatim quote);
        # 4096 truncated mid-JSON on real multi-page PDFs — verified live.
        max_tokens: int = 8192,
        require_grounding: bool = True,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens
        self._require_grounding = require_grounding
        self._fallback = HeuristicClaimStructurer()

    def structure(self, source: CvSource) -> list[ParClaim]:
        if not source.raw_text.strip():
            return []

        try:
            extracted = complete_json(
                self._client,
                system=_SYSTEM_PROMPT,
                messages=[LlmMessage.user(_build_prompt(source))],
                tier=self._tier,
                validator=_parse_claims,
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            # One unparseable/truncated response degrades this SOURCE to the heuristic
            # (usually fewer claims), never the whole nightly ingestion.
            logger.warning(
                "LLM structuring failed for source %s; falling back to heuristic: %s",
                source.external_ref,
                exc,
            )
            return self._fallback.structure(source)

        haystack = _normalize(source.raw_text) if self._require_grounding else ""
        claims: list[ParClaim] = []
        for item in extracted:
            if self._require_grounding and not _is_grounded(item.evidence_text, haystack):
                logger.warning(
                    "dropping ungrounded claim from source %s: evidence not found verbatim",
                    source.external_ref,
                )
                continue
            claims.append(
                ParClaim(
                    action=item.action,
                    problem=item.problem,
                    result=item.result,
                    evidence_text=item.evidence_text,
                    source_ref=source.external_ref,
                    source_type=source.source_type,
                )
            )
        return claims


def _build_prompt(source: CvSource) -> str:
    return (
        f"Document title: {source.title}\n"
        f"Source type: {source.source_type}\n"
        "--- BEGIN DOCUMENT ---\n"
        f"{source.raw_text}\n"
        "--- END DOCUMENT ---\n"
        "Extract the PAR claims per the rules."
    )


def _parse_claims(payload: Any) -> list[_ExtractedClaim]:
    """Validate the model's JSON into extracted claims; raise to trigger a JSON retry."""
    if not isinstance(payload, dict) or "claims" not in payload:
        raise ValueError("expected an object with a 'claims' array")
    raw_claims = payload["claims"]
    if not isinstance(raw_claims, list):
        raise ValueError("'claims' must be an array")

    result: list[_ExtractedClaim] = []
    for entry in raw_claims:
        if not isinstance(entry, dict):
            raise ValueError("each claim must be an object")
        action = entry.get("action")
        evidence = entry.get("evidence_text")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("each claim needs a non-empty string 'action'")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("each claim needs a non-empty string 'evidence_text'")
        result.append(
            _ExtractedClaim(
                action=action.strip(),
                evidence_text=evidence.strip(),
                problem=_opt_str(entry.get("problem")),
                result=_opt_str(entry.get("result")),
            )
        )
    return result


def _opt_str(value: Any) -> str | None:
    """Coerce an optional field to a trimmed string, treating blanks/non-strings as absent."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace runs, so quotes match despite reflowing."""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _is_grounded(evidence_text: str, normalized_source: str) -> bool:
    quote = _normalize(evidence_text)
    return bool(quote) and quote in normalized_source
