"""LLM-backed roster detection and chunk assignment.

Two calls, two tiers:

* :class:`LlmRosterProposer` (DEEP) — reads every normalized source and proposes the
  real-world entity roster (employers/roles and projects, with dates and aliases).
  This is the audit's load-bearing step: the four resume versions become one employer
  entry, the three-project case-study PDF becomes three projects, the degree form
  proposes nothing worth confirming.
* :class:`LlmChunkAssigner` (BULK) — tags each citation-sized chunk with the confirmed
  entity it evidences, or ``null`` when nothing genuinely matches.

Proposals are exactly that — nothing becomes a CV heading until a human confirms it
in the roster review, so a wrong proposal costs one click, not a corrupted ledger.
On any LLM failure both raise :class:`~app.domain.roster.RosterDetectionError` — loud
and skipped, never silently degraded (same policy as extraction).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.domain.claims import ASSIGNMENT_LLM, Experience, ExperienceKind, ExperienceSection
from app.domain.roster import (
    ProposedEntity,
    RosterDetectionError,
    SectionContent,
    SourceDocument,
)
from app.llm.client import LlmClient
from app.llm.errors import LlmError
from app.llm.json_completion import complete_json
from app.llm.types import LlmMessage, ModelTier

logger = logging.getLogger(__name__)

# Per-document text budget in the roster prompt: enough to identify entities without
# shipping whole documents (also keeps the call fast — see the extraction timeouts).
_DOC_TEXT_BUDGET = 4000
_CHUNK_TEXT_BUDGET = 800
# A section decision sees the heading trail plus a body summary — more context than an
# isolated 800-char fragment, still bounded.
_SECTION_TEXT_BUDGET = 1600

_ROSTER_SYSTEM = (
    "You are building the PROJECT ROSTER for one person's career evidence: the "
    "canonical list of real-world entities their resume should be organized under.\n"
    "\n"
    "An entity is either an employer_role ('Wellington Management - Technology "
    "Intern') or a project ('carrier-etl', 'PFAS contamination NLP pipeline'). "
    "Documents are NOT entities: a resume PDF usually describes SEVERAL employer "
    "roles; a case-study document may describe several projects; multiple files may "
    "describe the SAME entity (resume versions) - propose it once. A file that "
    "contains no career entity (a form, a transcript) proposes nothing.\n"
    "\n"
    "Rules:\n"
    "1. Use ONLY the provided sources. Never invent an employer, title, or date.\n"
    "2. One entry per distinct real-world entity, deduplicated across all sources.\n"
    "3. 'aliases' lists other names the sources use for the same entity (repo refs, "
    "abbreviations, product names) - they drive evidence assignment later.\n"
    "4. 'source_refs' lists the source_ref of every document that mentions the "
    "entity.\n"
    "5. 'dates' is the human-readable range exactly as evidenced (e.g. 'Jun-Aug "
    "2025'), or null.\n"
    "\n"
    'Reply with ONLY JSON: {"entities": [{"name": string, '
    '"kind": "employer_role"|"project", "subtitle": string|null, "dates": '
    'string|null, "aliases": [string], "source_refs": [string]}]}. No markdown.'
)

_ASSIGN_SYSTEM = (
    "You assign evidence chunks to the confirmed entities of a person's project "
    "roster. Each chunk of career-evidence text belongs to AT MOST one entity - the "
    "employer role or project the text is about.\n"
    "\n"
    "Rules:\n"
    "1. Assign by content, using the entity names and aliases.\n"
    "2. When a chunk genuinely matches no entity (boilerplate, contact headers, "
    "unrelated text), use null. NEVER force an assignment.\n"
    "3. A chunk describing work at an employer belongs to that employer_role even "
    "when it also names tools or generic topics.\n"
    "\n"
    'Reply with ONLY JSON: {"assignments": [{"chunk_index": int, '
    '"experience_id": int|null}]}. One entry per chunk. No markdown.'
)


def _section_for(kind: ExperienceKind) -> ExperienceSection:
    if kind is ExperienceKind.EMPLOYER_ROLE:
        return ExperienceSection.PROFESSIONAL_EXPERIENCE
    return ExperienceSection.PROJECTS_HACKATHONS


def _parse_entities(payload: Any) -> list[ProposedEntity]:
    if not isinstance(payload, dict) or not isinstance(payload.get("entities"), list):
        raise ValueError("expected an object with an 'entities' array")
    entities: list[ProposedEntity] = []
    for entry in payload["entities"]:
        if not isinstance(entry, dict):
            raise ValueError("each entity must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("each entity needs a non-empty 'name'")
        kind_raw = entry.get("kind")
        try:
            kind = ExperienceKind(str(kind_raw).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unknown entity kind {kind_raw!r}") from exc
        subtitle = entry.get("subtitle")
        dates = entry.get("dates")
        aliases_raw = entry.get("aliases", [])
        source_refs_raw = entry.get("source_refs", [])
        aliases = (
            tuple(str(a).strip() for a in aliases_raw if isinstance(a, str) and a.strip())
            if isinstance(aliases_raw, list)
            else ()
        )
        source_refs = (
            tuple(str(r).strip() for r in source_refs_raw if isinstance(r, str) and r.strip())
            if isinstance(source_refs_raw, list)
            else ()
        )
        entities.append(
            ProposedEntity(
                name=name.strip(),
                kind=kind,
                section=_section_for(kind),
                subtitle=subtitle.strip()
                if isinstance(subtitle, str) and subtitle.strip()
                else None,
                dates=dates.strip() if isinstance(dates, str) and dates.strip() else None,
                aliases=aliases,
                source_refs=source_refs,
            )
        )
    return entities


def _parse_assignments(payload: Any) -> dict[int, int | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("assignments"), list):
        raise ValueError("expected an object with an 'assignments' array")
    assignments: dict[int, int | None] = {}
    for entry in payload["assignments"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("chunk_index"), int):
            raise ValueError("each assignment needs an integer 'chunk_index'")
        experience_id = entry.get("experience_id")
        if experience_id is not None and not isinstance(experience_id, int):
            raise ValueError("'experience_id' must be an integer or null")
        assignments[entry["chunk_index"]] = experience_id
    return assignments


def _render_documents(documents: Sequence[SourceDocument]) -> str:
    parts = []
    for document in documents:
        text = document.text[:_DOC_TEXT_BUDGET]
        parts.append(
            f"[source] source_type={document.source_type} source_ref={document.source_ref}\n"
            f"title: {document.title}\n{text}"
        )
    return "\n---\n".join(parts)


class LlmRosterProposer:
    """Roster proposal via one DEEP-tier call over all normalized sources."""

    def __init__(self, client: LlmClient, *, tier: ModelTier = ModelTier.DEEP) -> None:
        self._client = client
        self._tier = tier

    def propose(self, documents: Sequence[SourceDocument]) -> list[ProposedEntity]:
        if not documents:
            return []
        prompt = (
            "Sources:\n"
            f"{_render_documents(documents)}\n\n"
            "Propose the deduplicated entity roster per the rules."
        )
        try:
            return complete_json(
                self._client,
                system=_ROSTER_SYSTEM,
                messages=[LlmMessage.user(prompt)],
                tier=self._tier,
                validator=_parse_entities,
                # Structured JSON output: adaptive thinking (the model default
                # when omitted) shares max_tokens with the reply and can consume
                # the whole budget - 'no text blocks' observed live 2026-07-13
                # (same failure PR #68 fixed for extraction calls).
                thinking={"type": "disabled"},
            )
        except LlmError as exc:
            raise RosterDetectionError(f"LLM roster detection failed: {exc}") from exc


def _roster_lines(roster: Sequence[Experience]) -> str:
    return "\n".join(
        f"- experience_id={entity.id} | kind={entity.kind.value} | name={entity.name}"
        + (f" | aliases={', '.join(entity.aliases)}" if entity.aliases else "")
        for entity in roster
    )


class LlmChunkAssigner:
    """Chunk→entity assignment via one BULK-tier call per batch of chunks.

    ``last_truncated`` counts the chunks cut to the prompt budget on the most recent
    call — a truncation is a visible event in the assignment report, never silent (F12).
    """

    method = ASSIGNMENT_LLM

    def __init__(self, client: LlmClient, *, tier: ModelTier = ModelTier.BULK) -> None:
        self._client = client
        self._tier = tier
        self.last_truncated = 0

    def assign(self, chunks: Sequence[str], roster: Sequence[Experience]) -> list[int | None]:
        self.last_truncated = sum(1 for chunk in chunks if len(chunk) > _CHUNK_TEXT_BUDGET)
        if self.last_truncated:
            logger.warning(
                "chunk assignment prompt truncated %d of %d chunk(s) to %d chars",
                self.last_truncated,
                len(chunks),
                _CHUNK_TEXT_BUDGET,
            )
        if not chunks or not roster:
            return [None] * len(chunks)
        roster_lines = _roster_lines(roster)
        chunk_lines = "\n---\n".join(
            f"[chunk {index}]\n{chunk[:_CHUNK_TEXT_BUDGET]}" for index, chunk in enumerate(chunks)
        )
        prompt = (
            f"Confirmed roster:\n{roster_lines}\n\nChunks:\n{chunk_lines}\n\n"
            "Assign every chunk per the rules."
        )
        try:
            parsed = complete_json(
                self._client,
                system=_ASSIGN_SYSTEM,
                messages=[LlmMessage.user(prompt)],
                tier=self._tier,
                validator=_parse_assignments,
                # Structured JSON output: adaptive thinking (the model default
                # when omitted) shares max_tokens with the reply and can consume
                # the whole budget - 'no text blocks' observed live 2026-07-13
                # (same failure PR #68 fixed for extraction calls).
                thinking={"type": "disabled"},
            )
        except LlmError as exc:
            raise RosterDetectionError(f"LLM chunk assignment failed: {exc}") from exc
        valid_ids = {entity.id for entity in roster}
        return [
            parsed.get(index) if parsed.get(index) in valid_ids else None
            for index in range(len(chunks))
        ]


_SECTION_SYSTEM = (
    "You assign document SECTIONS to the confirmed entities of a person's project "
    "roster. Each section is a top-level heading subtree of one career-evidence "
    "document: its heading trail plus a summary of its body. The whole section "
    "belongs to AT MOST one entity - the employer role or project it is about.\n"
    "\n"
    "Rules:\n"
    "1. The heading is the author's own label for the section - weigh it above "
    "body vocabulary. A section headed with entity X belongs to X even when its "
    "body names other tools or projects.\n"
    "2. When a section genuinely matches no entity (boilerplate, education, "
    "contact blocks), use null. NEVER force an assignment.\n"
    "\n"
    'Reply with ONLY JSON: {"assignments": [{"section_index": int, '
    '"experience_id": int|null}]}. One entry per section. No markdown.'
)


def _parse_section_assignments(payload: Any) -> dict[int, int | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("assignments"), list):
        raise ValueError("expected an object with an 'assignments' array")
    assignments: dict[int, int | None] = {}
    for entry in payload["assignments"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("section_index"), int):
            raise ValueError("each assignment needs an integer 'section_index'")
        experience_id = entry.get("experience_id")
        if experience_id is not None and not isinstance(experience_id, int):
            raise ValueError("'experience_id' must be an integer or null")
        assignments[entry["section_index"]] = experience_id
    return assignments


class LlmSectionAssigner:
    """Section→entity ownership via one BULK-tier call per document (H5).

    Sees the heading trail plus a bounded body summary per section — the context an
    isolated 800-char chunk prompt structurally lacked. ``last_truncated`` counts
    section bodies cut to the budget on the most recent call (F12: logged + reported,
    never silent).
    """

    method = ASSIGNMENT_LLM

    def __init__(self, client: LlmClient, *, tier: ModelTier = ModelTier.BULK) -> None:
        self._client = client
        self._tier = tier
        self.last_truncated = 0

    def assign_sections(
        self, sections: Sequence[SectionContent], roster: Sequence[Experience]
    ) -> list[int | None]:
        self.last_truncated = sum(
            1 for section in sections if len(section.body) > _SECTION_TEXT_BUDGET
        )
        if self.last_truncated:
            logger.warning(
                "section assignment prompt truncated %d of %d section bod(ies) to %d chars",
                self.last_truncated,
                len(sections),
                _SECTION_TEXT_BUDGET,
            )
        if not sections or not roster:
            return [None] * len(sections)
        section_lines = "\n---\n".join(
            f"[section {index}]\n"
            f"heading: {section.heading or '(no heading)'}\n"
            f"body: {section.body[:_SECTION_TEXT_BUDGET]}"
            for index, section in enumerate(sections)
        )
        prompt = (
            f"Confirmed roster:\n{_roster_lines(roster)}\n\nSections:\n{section_lines}\n\n"
            "Assign every section per the rules."
        )
        try:
            parsed = complete_json(
                self._client,
                system=_SECTION_SYSTEM,
                messages=[LlmMessage.user(prompt)],
                tier=self._tier,
                validator=_parse_section_assignments,
                # Structured JSON output: adaptive thinking (the model default
                # when omitted) shares max_tokens with the reply and can consume
                # the whole budget - 'no text blocks' observed live 2026-07-13
                # (same failure PR #68 fixed for extraction calls).
                thinking={"type": "disabled"},
            )
        except LlmError as exc:
            raise RosterDetectionError(f"LLM section assignment failed: {exc}") from exc
        valid_ids = {entity.id for entity in roster}
        return [
            parsed.get(index) if parsed.get(index) in valid_ids else None
            for index in range(len(sections))
        ]


__all__ = ["LlmChunkAssigner", "LlmRosterProposer", "LlmSectionAssigner"]
