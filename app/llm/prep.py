"""LLM-backed prep-packet generation (DEEP tier).

Drop-in for the domain :class:`~app.domain.interviews.PrepPacketGenerator` protocol.
The prompt only ever contains the user's real evidence — the application's tailored
materials and the Master CV's PAR claims — and instructs the model to ground every
line in it. Any failure falls back to the deterministic heuristic packet, so a flaky
call degrades to a plainer packet, never to a missing one.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.applications import Application
from app.domain.cv import MasterCv
from app.domain.interviews import (
    HeuristicPrepPacketGenerator,
    Interview,
    PrepPacket,
)
from app.domain.matching import build_profile
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger("app.llm.prep")

_SYSTEM = (
    "You prepare a candidate for a job interview, using ONLY the evidence provided "
    "(their tailored application and career claims). Never invent experience, projects, "
    "metrics, or company facts that are not in the evidence. Produce a focused markdown "
    "prep packet with sections: likely interview topics, evidence to lead with (grounded "
    "in the provided claims), anticipated questions with suggested framings, and sharp "
    "questions to ask the interviewer.\n"
    'Reply with ONLY JSON of the form {"content": "<markdown packet>"}. '
    "No prose outside the JSON, no code fences."
)


class LlmPrepPacketGenerator:
    """DEEP-tier prep packets; heuristic fallback on any failure."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.DEEP,
        max_tokens: int = 3000,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens
        self._fallback = HeuristicPrepPacketGenerator()

    def generate(
        self,
        interview: Interview,
        master_cv: MasterCv,
        application: Application | None,
    ) -> PrepPacket:
        try:
            content = complete_json(
                self._client,
                system=_SYSTEM,
                messages=[LlmMessage.user(_prompt(interview, master_cv, application))],
                tier=self._tier,
                validator=_parse_content,
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.warning(
                "LLM prep generation failed for interview %d; falling back to heuristic: %s",
                interview.id,
                exc,
            )
            return self._fallback.generate(interview, master_cv, application)
        return PrepPacket(interview_id=interview.id, content=content)


def _prompt(interview: Interview, master_cv: MasterCv, application: Application | None) -> str:
    lines = [
        f"Interview: {interview.job_title or 'role not stated'} at {interview.company}.",
        "",
    ]
    if application is not None:
        lines += [
            "The candidate's tailored application for this job:",
            f"Summary: {application.materials.summary}",
            "Highlights:",
            *[f"- {h}" for h in application.materials.highlights],
            "",
        ]
    profile = build_profile(master_cv)
    if profile.summary:
        lines += ["Candidate career evidence (PAR claims):", profile.summary, ""]
    lines.append("Write the prep packet.")
    return "\n".join(lines)


def _parse_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("'content' must be a non-empty string")
    return content.strip()
