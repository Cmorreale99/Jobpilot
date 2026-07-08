"""LLM-backed tailoring and outreach drafting (DEEP tier).

Drop-in implementations of the domain :class:`~app.domain.tailoring.MaterialsTailorer`
and :class:`~app.domain.outreach.OutreachDrafter` protocols. Living in ``app/llm/``
keeps ``domain/`` free of any LLM import.

Truthfulness is enforced structurally, not just by prompt:

* The tailorer addresses Master CV claims by positional id (``c0``, ``c1``, ...) and the
  model *selects* highlight ids — highlights are then rendered from the real claims, so
  a hallucinated id is simply dropped and invented evidence cannot appear as a highlight.
* The drafter only ever sees a contact that research actually found; with none, the
  prompt requires a generic greeting.

Both fall back to their deterministic heuristic counterparts on any LLM failure — a
flaky call degrades to a plainer draft, never to a crash or a lost application.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.cv import MasterCv, ParClaim
from app.domain.jobs import Job, JobMatch
from app.domain.outreach import Contact, HeuristicOutreachDrafter, OutreachMessage
from app.domain.tailoring import (
    MAX_HIGHLIGHTS,
    HeuristicMaterialsTailorer,
    TailoredMaterials,
    claim_number_source,
    claim_ref_id,
    render_claim,
    unsupported_numbers,
)
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger("app.llm.drafting")

_MAX_DESC_CHARS = 2000

_TAILOR_SYSTEM = (
    "You tailor application materials for a candidate, using ONLY the numbered career "
    "claims provided — never invent experience, skills, or metrics the claims do not "
    "contain. Select the claims most relevant to the posting as highlights (by id), and "
    "write a short fit summary and a concise, specific cover letter grounded in those "
    "claims.\n"
    'Reply with ONLY JSON of the form {"summary": "<1-2 sentences>", '
    f'"highlight_ids": [<up to {MAX_HIGHLIGHTS} ids like "c0">], '
    '"cover_letter": "<3 short paragraphs max>"}. No prose, no code fences.'
)

_OUTREACH_SYSTEM = (
    "You draft a short, professional outreach email about one job application, grounded "
    "ONLY in the tailored materials provided — never invent experience or claims. If a "
    "researched contact is given, address them by name; if none is given, address the "
    "company's hiring team generically and do NOT invent a person or a name. Keep it "
    "under 150 words, specific, and free of flattery.\n"
    'Reply with ONLY JSON of the form {"subject": "<subject line>", '
    '"body": "<the email body>"}. No prose, no code fences.'
)


class LlmMaterialsTailorer:
    """DEEP-tier tailorer with id-grounded highlights; heuristic fallback on failure."""

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
        self._fallback = HeuristicMaterialsTailorer()

    def tailor(self, master_cv: MasterCv, match: JobMatch) -> TailoredMaterials:
        if not master_cv.claims:
            return self._fallback.tailor(master_cv, match)
        by_id = {f"c{i}": claim for i, claim in enumerate(master_cv.claims)}
        try:
            summary, highlight_ids, cover_letter = complete_json(
                self._client,
                system=_TAILOR_SYSTEM,
                # The numbered-claims block is identical for every application tailored
                # in a run (same Master CV); as cached context the first call writes the
                # prompt cache and the remaining top-N calls read it at ~0.1x.
                cached_context=_claims_block(by_id),
                messages=[LlmMessage.user(_tailor_prompt(match.job))],
                tier=self._tier,
                validator=_parse_materials,
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.warning(
                "LLM tailoring failed for job %s; falling back to heuristic: %s",
                match.job.external_id,
                exc,
            )
            return self._fallback.tailor(master_cv, match)

        # Ground highlights in the real claims: unknown or duplicate ids are dropped.
        seen: set[str] = set()
        selected: list[ParClaim] = []
        for claim_id in highlight_ids:
            claim = by_id.get(claim_id)
            if claim is None or claim_id in seen:
                continue
            seen.add(claim_id)
            selected.append(claim)
            if len(selected) >= MAX_HIGHLIGHTS:
                break
        if not selected:
            logger.warning(
                "LLM tailoring for job %s selected no valid claims; using heuristic.",
                match.job.external_id,
            )
            return self._fallback.tailor(master_cv, match)

        # Number-factuality gate (audit §9; §6): every number in the generated prose must
        # come from a selected claim's CITED EVIDENCE or the posting itself — never from the
        # (possibly composed) component text. An invented metric fails closed to the
        # deterministic tailorer — it never leaves the machine.
        allowed = [
            *(claim_number_source(claim) for claim in selected),
            f"{match.job.title} {match.job.description}",
        ]
        invented = unsupported_numbers(f"{summary}\n{cover_letter}", allowed)
        if invented:
            logger.warning(
                "LLM tailoring for job %s invented unsupported numbers %s; using heuristic.",
                match.job.external_id,
                invented,
            )
            return self._fallback.tailor(master_cv, match)

        return TailoredMaterials(
            summary=summary,
            highlights=tuple(render_claim(claim) for claim in selected),
            cover_letter=cover_letter,
            highlight_claim_ids=tuple(claim_ref_id(claim) or 0 for claim in selected),
            highlight_refs=tuple(claim.source_ref for claim in selected),
        )


class LlmOutreachDrafter:
    """DEEP-tier outreach drafter; heuristic fallback on failure."""

    def __init__(
        self,
        client: LlmClient,
        *,
        tier: ModelTier = ModelTier.DEEP,
        max_tokens: int = 1024,
    ) -> None:
        self._client = client
        self._tier = tier
        self._max_tokens = max_tokens
        self._fallback = HeuristicOutreachDrafter()

    def draft(
        self, materials: TailoredMaterials, job: Job, contact: Contact | None
    ) -> OutreachMessage:
        try:
            subject, body = complete_json(
                self._client,
                system=_OUTREACH_SYSTEM,
                messages=[LlmMessage.user(_outreach_prompt(materials, job, contact))],
                tier=self._tier,
                validator=_parse_message,
                max_tokens=self._max_tokens,
            )
        except LlmError as exc:
            logger.warning(
                "LLM outreach drafting failed for job %s; falling back to heuristic: %s",
                job.external_id,
                exc,
            )
            return self._fallback.draft(materials, job, contact)

        # Outreach body number gate (§6): the one artifact that actually leaves the machine
        # had no number gate at all. Every figure in the subject or body must come from the
        # already-grounded tailored materials or the posting — an invented metric fails
        # closed to the heuristic drafter, which quotes the real highlights verbatim.
        allowed = [
            materials.summary,
            materials.cover_letter,
            *materials.highlights,
            f"{job.title} {job.description}",
        ]
        invented = unsupported_numbers(f"{subject}\n{body}", allowed)
        if invented:
            logger.warning(
                "LLM outreach for job %s invented unsupported numbers %s; using heuristic.",
                job.external_id,
                invented,
            )
            return self._fallback.draft(materials, job, contact)
        return OutreachMessage(subject=subject, body=body)


# --- prompts ---------------------------------------------------------------------


def _claims_block(by_id: dict[str, ParClaim]) -> str:
    lines = ["Candidate career claims:"]
    for claim_id, claim in by_id.items():
        segments = []
        if claim.problem:
            segments.append(f"Problem: {claim.problem}")
        segments.append(f"Action: {claim.action}")
        if claim.result:
            segments.append(f"Result: {claim.result}")
        lines.append(f"- id={claim_id} | " + " | ".join(segments))
    return "\n".join(lines)


def _tailor_prompt(job: Job) -> str:
    return (
        "Job posting:\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Description: {_truncate(job.description, _MAX_DESC_CHARS)}\n\n"
        "Tailor the materials."
    )


def _outreach_prompt(materials: TailoredMaterials, job: Job, contact: Contact | None) -> str:
    lines = [
        "Job posting:",
        f"Title: {job.title}",
        f"Company: {job.company}",
        "",
        "Tailored materials:",
        f"Summary: {materials.summary}",
        "Highlights:",
        *[f"- {h}" for h in materials.highlights],
    ]
    if contact is not None:
        contact_line = contact.name if not contact.title else f"{contact.name} ({contact.title})"
        lines += ["", f"Researched contact: {contact_line}"]
    else:
        lines += ["", "Researched contact: none found — address the hiring team generically."]
    lines += ["", "Draft the outreach email."]
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# --- response parsing ------------------------------------------------------------


def _parse_materials(payload: Any) -> tuple[str, list[str], str]:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    summary = _required_str(payload, "summary")
    cover_letter = _required_str(payload, "cover_letter")
    raw_ids = payload.get("highlight_ids")
    if not isinstance(raw_ids, list):
        raise ValueError("'highlight_ids' must be an array")
    ids = [item for item in raw_ids if isinstance(item, str) and item.strip()]
    return summary, ids, cover_letter


def _parse_message(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    return _required_str(payload, "subject"), _required_str(payload, "body")


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return value.strip()
