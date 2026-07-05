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
from typing import Protocol, runtime_checkable

from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    Experience,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
)
from app.domain.matching import tokenize


@dataclass(frozen=True)
class SourceDocument:
    """One normalized source artifact feeding detection and assignment."""

    source_type: str  # drive | github_readme | github_commit
    source_ref: str  # doc id / repo ref / repo@sha
    title: str
    text: str  # normalized (see domain/text_normalization.py)


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

    Returns one entry per chunk text, aligned by index.
    """

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


class HeuristicChunkAssigner:
    """Name/alias token-overlap scoring; a tie or zero overlap is ``None``.

    Deliberately conservative: an unassigned chunk never feeds extraction, which is
    strictly better than feeding it to the wrong project.
    """

    def assign(self, chunks: Sequence[str], roster: Sequence[Experience]) -> list[int | None]:
        profiles = [
            (
                entity.id,
                frozenset(
                    token for name in (entity.name, *entity.aliases) for token in tokenize(name)
                ),
            )
            for entity in roster
        ]
        assignments: list[int | None] = []
        for chunk in chunks:
            chunk_tokens = set(tokenize(chunk))
            scored = sorted(
                ((len(tokens & chunk_tokens), entity_id) for entity_id, tokens in profiles),
                reverse=True,
            )
            if not scored or scored[0][0] == 0:
                assignments.append(None)
            elif len(scored) > 1 and scored[0][0] == scored[1][0]:
                assignments.append(None)  # ambiguous: refuse rather than guess
            else:
                assignments.append(scored[0][1])
        return assignments


__all__ = [
    "ChunkAssigner",
    "HeuristicChunkAssigner",
    "HeuristicRosterProposer",
    "ProposedEntity",
    "RosterDetectionError",
    "RosterProposer",
    "SourceDocument",
]
