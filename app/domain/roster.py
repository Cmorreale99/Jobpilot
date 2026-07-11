"""The project roster: entity proposals and chunk→entity assignment (pure logic).

The roster layer is the V2 audit's root-cause fix: an "experience" must be a real-world
entity (an employer role or a project), not a file. Detection PROPOSES entities from
the evidence; a HUMAN confirms/merges/renames/discards them; chunk assignment then
scopes every evidence chunk to one confirmed entity — and extraction only ever sees
one entity's chunks at a time, so cross-project contamination becomes unrepresentable.

Two protocols with deterministic defaults here (LLM-backed versions in
``app/llm/roster.py``):

* :class:`RosterProposer` — proposes entities from the normalized source documents.
  The heuristic default proposes one entity per source (repo/doc) — exactly the old
  file-shaped behavior, but now landing as *proposals* the human reshapes instead of
  silently becoming CV headings.
* :class:`ChunkAssigner` — assigns one chunk to a confirmed entity (or honestly
  ``None``). The heuristic scores name/alias token overlap and refuses ties.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Protocol, runtime_checkable

from app.domain.claims import (
    ASSIGNMENT_HEURISTIC,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    Claim,
    ClaimField,
    ClaimStatus,
    Experience,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    StoredEvidence,
)
from app.domain.matching import tokenize


@dataclass(frozen=True)
class SourceDocument:
    """One normalized source artifact feeding detection and assignment.

    Metadata fields ride along from the client (H2) so the capture layer and any
    downstream audit can see them; they default to ``None`` where a source type has
    no such concept (commits have no MIME type, READMEs no size).
    """

    source_type: str  # drive | github_readme | github_commit | upload
    source_ref: str  # doc id / repo ref / repo@sha / filename
    title: str
    text: str  # normalized (see domain/text_normalization.py)
    mime_type: str | None = None
    modified_time: datetime | None = None
    size_bytes: int | None = None
    # As-received text (H2/H5): what structure-aware chunking derives elements from.
    # ``None`` for callers that only have normalized text — they fall back to the
    # flat-text path.
    raw_text: str | None = None


@dataclass(frozen=True)
class ProposedEntity:
    """One roster proposal, pre-persistence."""

    name: str
    kind: ExperienceKind
    section: ExperienceSection
    subtitle: str | None = None
    dates: str | None = None
    aliases: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = field(default=())

    def to_seed(self) -> ExperienceSeed:
        return ExperienceSeed(
            name=self.name,
            section=self.section,
            subtitle=self.subtitle,
            dates=self.dates,
            kind=self.kind,
            aliases=self.aliases,
        )


class RosterDetectionError(RuntimeError):
    """Roster detection or chunk assignment failed outright (e.g. the LLM call).

    Raised instead of degrading silently — a wrong or empty roster misscopes every
    downstream claim, so the operator must see the failure.
    """


@runtime_checkable
class RosterProposer(Protocol):
    """Proposes roster entities from the user's normalized sources."""

    def propose(self, documents: Sequence[SourceDocument]) -> list[ProposedEntity]: ...


@runtime_checkable
class ChunkAssigner(Protocol):
    """Assigns chunks to confirmed roster entities (``None`` = honestly unassigned).

    Returns one entry per chunk text, aligned by index. Implementations carry a
    ``method`` label (an ``ASSIGNMENT_*`` value) so every assignment they make is
    stamped with HOW it was decided (hardening H1).
    """

    method: str

    def assign(self, chunks: Sequence[str], roster: Sequence[Experience]) -> list[int | None]: ...


class HeuristicRosterProposer:
    """One proposal per source — the deterministic, mock-first default.

    Repos become ``project`` proposals (their repo_ref as an alias, so commit evidence
    assigns to them trivially); Drive documents become ``employer_role`` proposals
    named after their title. This intentionally reproduces the old file-shaped
    boundaries — as *proposals*: the human merges the four resume versions, renames
    entities, and discards the junk in roster review instead of on the rendered CV.
    """

    def propose(self, documents: Sequence[SourceDocument]) -> list[ProposedEntity]:
        proposals: list[ProposedEntity] = []
        seen: set[str] = set()
        for document in documents:
            if document.source_type == SOURCE_GITHUB_COMMIT:
                continue  # commits evidence a repo's entity; they don't propose one
            key = document.title.casefold()
            if not document.title.strip() or key in seen:
                continue
            seen.add(key)
            if document.source_type == SOURCE_GITHUB_README:
                proposals.append(
                    ProposedEntity(
                        name=document.title,
                        kind=ExperienceKind.PROJECT,
                        section=ExperienceSection.PROJECTS_HACKATHONS,
                        aliases=(document.source_ref,),
                        source_refs=(document.source_ref,),
                    )
                )
            else:
                proposals.append(
                    ProposedEntity(
                        name=document.title,
                        kind=ExperienceKind.EMPLOYER_ROLE,
                        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
                        source_refs=(document.source_ref,),
                    )
                )
        return proposals


def _entity_profiles(roster: Sequence[Experience]) -> list[tuple[int, frozenset[str]]]:
    return [
        (
            entity.id,
            frozenset(token for name in (entity.name, *entity.aliases) for token in tokenize(name)),
        )
        for entity in roster
    ]


def _best_entity(text: str, profiles: Sequence[tuple[int, frozenset[str]]]) -> int | None:
    """The unique highest token-overlap entity; a tie or zero overlap is ``None``."""
    tokens = set(tokenize(text))
    scored = sorted(
        ((len(entity_tokens & tokens), entity_id) for entity_id, entity_tokens in profiles),
        reverse=True,
    )
    if not scored or scored[0][0] == 0:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous: refuse rather than guess
    return scored[0][1]


class HeuristicChunkAssigner:
    """Name/alias token-overlap scoring; a tie or zero overlap is ``None``.

    Deliberately conservative: an unassigned chunk never feeds extraction, which is
    strictly better than feeding it to the wrong project.
    """

    method = ASSIGNMENT_HEURISTIC

    def assign(self, chunks: Sequence[str], roster: Sequence[Experience]) -> list[int | None]:
        profiles = _entity_profiles(roster)
        return [_best_entity(chunk, profiles) for chunk in chunks]


# --- section-scoped ownership (H5) ----------------------------------------------------


@dataclass(frozen=True)
class SectionContent:
    """One top-level section subtree as the section assigner sees it.

    ``heading`` is the root heading's text (``None`` for the preamble/structureless
    pseudo-section); ``body`` is the subtree's remaining text in document order.
    """

    heading: str | None
    body: str


@runtime_checkable
class SectionAssigner(Protocol):
    """Decides ownership once per top-level section subtree (``None`` = unowned).

    The H5 fix for the Cooper misassignment: a Result paragraph with zero entity
    tokens inherits its section's owner instead of being re-guessed in isolation.
    Returns one entry per section, aligned by index; ``method`` labels how the
    *section decision* was made (chunks inheriting it are stamped ``section``).
    """

    method: str

    def assign_sections(
        self, sections: Sequence[SectionContent], roster: Sequence[Experience]
    ) -> list[int | None]: ...


class HeuristicSectionAssigner:
    """Heading tokens decide first; only a silent heading falls back to the body.

    The heading is the author's own ownership declaration, so it outranks body
    vocabulary — a body mentioning another project's tools can never outvote the
    section title (heading-context beats vocabulary). Both stages refuse ties.
    """

    method = ASSIGNMENT_HEURISTIC

    def assign_sections(
        self, sections: Sequence[SectionContent], roster: Sequence[Experience]
    ) -> list[int | None]:
        profiles = _entity_profiles(roster)
        assignments: list[int | None] = []
        for section in sections:
            target = _best_entity(section.heading, profiles) if section.heading else None
            if target is None:
                target = _best_entity(section.body, profiles)
            assignments.append(target)
        return assignments


# --- cross-entity evidence overlap (V3 Phase 1, docs/ARCHITECTURE_V3.md §3.7) ---------

# A shared chunk shorter than this never signals: short fragments (section headers,
# tool lists, boilerplate lines) legitimately repeat across distinct projects.
MIN_SHARED_CHUNK_CHARS = 60


@dataclass(frozen=True)
class EntityOverlap:
    """Two entities sharing evidence — a merge prompt. A RELATION, never a status."""

    experience_a_id: int
    experience_b_id: int
    shared_outcome_quotes: tuple[str, ...] = ()
    shared_chunk_texts: tuple[str, ...] = ()


def _normalized(text: str) -> str:
    return " ".join(text.split())


def detect_entity_overlaps(
    claims: Sequence[Claim],
    evidence: Sequence[StoredEvidence],
) -> list[EntityOverlap]:
    """Deterministic cross-entity duplicate detection — the §3.7 dedupe pass.

    Per-entity synthesis structurally cannot see a duplicate across entities (each
    call's input contains exactly one entity), so this runs as a corpus-wide pass
    BEFORE synthesis. Two exact-text signals, no embeddings, no LLM:

    * **Shared outcome quote**: the same normalized ``outcome_quote`` backs a Result
      under two different entities — the live proof case: "Top 3 out of 100+ teams"
      sitting under both a hackathon project and the portfolio container that
      mentions it. Within-group span dedupe can't catch this; nothing else can.
    * **Shared chunk text**: the same normalized chunk text (at least
      :data:`MIN_SHARED_CHUNK_CHARS` chars — short repeated fragments never signal)
      is assigned to two different entities.

    Rejected claims don't signal (the human already ruled that content out). Two
    projects merely sharing tools or an employer do NOT overlap — the match is
    exact normalized text, not vocabulary. Each detected pair is a merge prompt
    resolved through the existing roster merge, never a status on either entity.
    """
    quote_owners: dict[str, set[int]] = {}
    for claim in claims:
        if claim.status is ClaimStatus.REJECTED:
            continue
        for link in claim.evidence:
            if link.field is not ClaimField.RESULT or not (link.outcome_quote or "").strip():
                continue
            quote_owners.setdefault(_normalized(link.outcome_quote or ""), set()).add(
                claim.experience_id
            )

    chunk_owners: dict[str, set[int]] = {}
    for stored in evidence:
        if stored.experience_id is None:
            continue
        text = _normalized(stored.chunk_text)
        if len(text) < MIN_SHARED_CHUNK_CHARS:
            continue
        chunk_owners.setdefault(text, set()).add(stored.experience_id)

    shared_quotes: dict[tuple[int, int], set[str]] = {}
    for quote, owners in quote_owners.items():
        for pair in combinations(sorted(owners), 2):
            shared_quotes.setdefault(pair, set()).add(quote)
    shared_chunks: dict[tuple[int, int], set[str]] = {}
    for text, owners in chunk_owners.items():
        for pair in combinations(sorted(owners), 2):
            shared_chunks.setdefault(pair, set()).add(text)

    return [
        EntityOverlap(
            experience_a_id=a,
            experience_b_id=b,
            shared_outcome_quotes=tuple(sorted(shared_quotes.get((a, b), ()))),
            shared_chunk_texts=tuple(sorted(shared_chunks.get((a, b), ()))),
        )
        for a, b in sorted(shared_quotes.keys() | shared_chunks.keys())
    ]


__all__ = [
    "MIN_SHARED_CHUNK_CHARS",
    "ChunkAssigner",
    "EntityOverlap",
    "HeuristicChunkAssigner",
    "HeuristicRosterProposer",
    "HeuristicSectionAssigner",
    "ProposedEntity",
    "RosterDetectionError",
    "RosterProposer",
    "SectionAssigner",
    "SectionContent",
    "SourceDocument",
    "detect_entity_overlaps",
]
