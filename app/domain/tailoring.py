"""Tailored application materials derived from the Master CV — pure and deterministic.

Tailoring selects the Master CV claims most relevant to one job and renders them into
materials (summary, highlights, cover letter). The invariant carried over from
:mod:`app.domain.cv` holds here: **materials are derived from real claims only** — the
heuristic renders claim text verbatim and the LLM-backed tailorer (``app/llm/``) must
reference claims by id, so neither can invent experience.

The :class:`MaterialsTailorer` protocol mirrors ``JobScorer``/``JobReranker``: heuristic
default here, LLM-backed drop-in in ``app/llm/drafting.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.cv import MasterCv, ParClaim
from app.domain.jobs import JobMatch
from app.domain.matching import tokenize

# Bounds keeping materials focused: enough evidence to be convincing, short enough to read.
MAX_HIGHLIGHTS = 5
_FALLBACK_HIGHLIGHTS = 3


@dataclass(frozen=True)
class TailoredMaterials:
    """Materials tailored to one job: a fit summary, evidence highlights, a cover letter.

    ``highlights`` are rendered PAR claims (real evidence, never invented); the summary
    and cover letter are built around them.
    """

    summary: str
    highlights: tuple[str, ...]
    cover_letter: str

    def to_json(self) -> dict[str, Any]:
        """Serialize to the structure stored in ``applications.materials_json``."""
        return {
            "summary": self.summary,
            "highlights": list(self.highlights),
            "cover_letter": self.cover_letter,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TailoredMaterials:
        return cls(
            summary=str(data.get("summary", "")),
            highlights=tuple(str(h) for h in data.get("highlights", [])),
            cover_letter=str(data.get("cover_letter", "")),
        )


class MaterialsTailorer(Protocol):
    """Turns (Master CV, one deep-ranked match) into tailored materials."""

    def tailor(self, master_cv: MasterCv, match: JobMatch) -> TailoredMaterials: ...


def render_claim(claim: ParClaim) -> str:
    """Render one PAR claim as a single evidence line, verbatim from its fields."""
    line = claim.action
    if claim.result:
        line = f"{line} — {claim.result}"
    return line


def select_claims(
    master_cv: MasterCv, match: JobMatch, limit: int = MAX_HIGHLIGHTS
) -> list[ParClaim]:
    """Pick the claims most relevant to the job by keyword overlap (stable, deterministic).

    Claims with zero overlap are excluded; if nothing overlaps, the first few claims are
    used instead — arbitrary but still real evidence, never fabricated filler.
    """
    job = match.job
    job_tokens = set(tokenize(f"{job.title} {job.description}"))
    scored: list[tuple[int, int, ParClaim]] = []
    for index, claim in enumerate(master_cv.claims):
        claim_tokens = set(
            tokenize(" ".join([claim.action, claim.problem or "", claim.result or ""]))
        )
        scored.append((len(claim_tokens & job_tokens), index, claim))
    relevant = sorted((entry for entry in scored if entry[0] > 0), key=lambda e: (-e[0], e[1]))[
        :limit
    ]
    if relevant:
        return [claim for _, _, claim in relevant]
    return list(master_cv.claims[:_FALLBACK_HIGHLIGHTS])


class HeuristicMaterialsTailorer:
    """Deterministic template tailorer — the mock-first default.

    Every sentence is assembled from job fields and verbatim claim renderings, so the
    output is fully explainable and repeatable.
    """

    def tailor(self, master_cv: MasterCv, match: JobMatch) -> TailoredMaterials:
        job = match.job
        claims = select_claims(master_cv, match)
        highlights = tuple(render_claim(c) for c in claims)
        terms = ", ".join(match.matched_terms[:6])
        summary = f"Fit for {job.title} at {job.company}" + (
            f" — relevant strengths: {terms}." if terms else "."
        )
        body_lines = [
            f"I am applying for the {job.title} role at {job.company}.",
            "Relevant evidence from my track record:",
            *[f"- {h}" for h in highlights],
            "I would welcome the chance to discuss how this experience maps to the role.",
        ]
        return TailoredMaterials(
            summary=summary,
            highlights=highlights,
            cover_letter="\n".join(body_lines),
        )
