"""The Master CV consumer model: PAR claims with provenance.

V2.1: the Master CV is exclusively a snapshot of human-approved claims
(``domain/master_cv_snapshot.py``); :func:`~app.domain.master_cv_snapshot.master_cv_from_snapshot`
adapts a snapshot into this shape for the consumers — matching, tailoring, and prep
generation. Each :class:`ParClaim` traces to its reviewed claim (``source_ref`` =
``claim:<id>``), so everything derived from the Master CV is auditable back to a
human decision.

The V1 builder/structurer path that used to live here (structure raw source text into
claims with no review) was DELETED per the architecture audit (docs/V2_AUDIT.md §11.3):
claims are only ever produced by the extraction + review pipeline.

Pure data — no imports from ``integrations/`` implementations, ``llm/``, or MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class CvSource:
    """Provenance record for one ingested artifact (maps to the ``cv_sources`` table)."""

    source_type: str  # e.g. "gdrive"
    external_ref: str  # Drive file id / fixture id
    title: str
    mime_type: str
    raw_text: str
    ingested_at: datetime
    modified_time: datetime | None = None


@dataclass(frozen=True)
class ParClaim:
    """A Problem-Action-Result framed claim traced to a single source.

    ``action`` is always present (it is the load-bearing part of the evidence).
    ``problem`` and ``result`` are optional: absent rather than fabricated when the
    evidence does not supply them. For snapshot-adapted claims ``source_type`` is
    ``approved_claim`` and ``source_ref`` is ``claim:<id>``.
    """

    action: str
    source_ref: str
    source_type: str = "gdrive"
    problem: str | None = None
    result: str | None = None
    evidence_text: str = ""


@dataclass
class MasterCv:
    """A versioned, PAR-framed Master CV with full provenance."""

    claims: list[ParClaim] = field(default_factory=list)
    sources: list[CvSource] = field(default_factory=list)
    version: int = 1


__all__ = ["CvSource", "MasterCv", "ParClaim"]
