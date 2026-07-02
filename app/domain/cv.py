"""Master CV domain model: provenance, PAR claims, and the builder.

Pure logic — no imports from ``integrations/`` implementations, ``llm/``, or MCP. The
service layer converts a :class:`DriveDocument` into a :class:`CvSource` and hands a
list of them here. Everything downstream (structuring into PAR claims, versioning) is
deterministic and testable offline.

Two invariants this module enforces:

* **No invented experience.** Claims are only ever derived from source text; the default
  structurer copies evidence verbatim and never synthesizes content.
* **Provenance is preserved.** Every :class:`ParClaim` carries the ``source_ref`` of the
  :class:`CvSource` it came from, so any resume line traces back to a Drive file id.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

# --- Provenance ------------------------------------------------------------------


@dataclass(frozen=True)
class CvSource:
    """Provenance record for one ingested artifact (maps to the ``cv_sources`` table).

    Captures where evidence came from and the exact text extracted, so the Master CV is
    always auditable back to its source.
    """

    source_type: str  # e.g. "gdrive"
    external_ref: str  # Drive file id / fixture id
    title: str
    mime_type: str
    raw_text: str
    ingested_at: datetime
    modified_time: datetime | None = None


# --- PAR claims ------------------------------------------------------------------


@dataclass(frozen=True)
class ParClaim:
    """A Problem-Action-Result framed claim traced to a single source.

    ``action`` is always present (it is the load-bearing part of the evidence).
    ``problem`` and ``result`` are optional: absent rather than fabricated when the
    source text does not supply them.
    """

    action: str
    source_ref: str
    source_type: str = "gdrive"
    problem: str | None = None
    result: str | None = None
    evidence_text: str = ""


class ClaimStructurer(Protocol):
    """Turns one :class:`CvSource` into PAR claims.

    The default implementation is deterministic and offline. A real LLM-backed
    structurer (``app/llm/``) can be injected later without changing the builder.
    """

    def structure(self, source: CvSource) -> list[ParClaim]: ...


_LABEL_RE = re.compile(r"^(problem|action|result)\s*:\s*(.+)$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")


class HeuristicClaimStructurer:
    """Deterministic PAR extractor used for the mock-first pipeline.

    Parses two evidence shapes without ever inventing content:

    * Explicit ``Problem:``/``Action:``/``Result:`` blocks -> one claim per block.
    * Bullet lists -> one action-only claim per bullet.

    Anything else (headers, prose) is ignored rather than turned into a claim.
    """

    def structure(self, source: CvSource) -> list[ParClaim]:
        claims: list[ParClaim] = []
        for block in _split_blocks(source.raw_text):
            labels = _extract_labels(block)
            if "action" in labels:
                claims.append(
                    ParClaim(
                        action=labels["action"],
                        problem=labels.get("problem"),
                        result=labels.get("result"),
                        evidence_text=block.strip(),
                        source_ref=source.external_ref,
                        source_type=source.source_type,
                    )
                )
                continue
            for line in block.splitlines():
                bullet = _BULLET_RE.match(line.strip())
                if bullet:
                    text = bullet.group(1).strip()
                    claims.append(
                        ParClaim(
                            action=text,
                            evidence_text=text,
                            source_ref=source.external_ref,
                            source_type=source.source_type,
                        )
                    )
        return claims


def _split_blocks(text: str) -> list[str]:
    """Split text into blocks separated by one or more blank lines."""
    return [block for block in re.split(r"\n\s*\n", text) if block.strip()]


def _extract_labels(block: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for line in block.splitlines():
        match = _LABEL_RE.match(line.strip())
        if match:
            labels[match.group(1).lower()] = match.group(2).strip()
    return labels


# --- Master CV -------------------------------------------------------------------


@dataclass
class MasterCv:
    """A versioned, PAR-framed Master CV with full provenance."""

    claims: list[ParClaim] = field(default_factory=list)
    sources: list[CvSource] = field(default_factory=list)
    version: int = 1

    def to_content_json(self) -> dict[str, object]:
        """Serialize to the structure stored in ``master_cv.content_json``."""
        return {
            "version": self.version,
            "claims": [
                {
                    "problem": c.problem,
                    "action": c.action,
                    "result": c.result,
                    "evidence_text": c.evidence_text,
                    "source_type": c.source_type,
                    "source_ref": c.source_ref,
                }
                for c in self.claims
            ],
            "sources": [
                {
                    "source_type": s.source_type,
                    "external_ref": s.external_ref,
                    "title": s.title,
                    "mime_type": s.mime_type,
                    "modified_time": s.modified_time.isoformat() if s.modified_time else None,
                    "ingested_at": s.ingested_at.isoformat(),
                }
                for s in self.sources
            ],
        }

    @classmethod
    def from_content_json(cls, data: dict[str, Any]) -> MasterCv:
        """Rebuild a Master CV from stored ``content_json``.

        Source ``raw_text`` is not carried in ``content_json`` (it lives authoritatively
        in the ``cv_sources`` table), so reconstructed sources have empty ``raw_text``.
        """
        claims = [
            ParClaim(
                action=c["action"],
                source_ref=c["source_ref"],
                source_type=c.get("source_type", "gdrive"),
                problem=c.get("problem"),
                result=c.get("result"),
                evidence_text=c.get("evidence_text", ""),
            )
            for c in data.get("claims", [])
        ]
        sources = [
            CvSource(
                source_type=s["source_type"],
                external_ref=s["external_ref"],
                title=s["title"],
                mime_type=s["mime_type"],
                raw_text="",
                ingested_at=datetime.fromisoformat(s["ingested_at"]),
                modified_time=(
                    datetime.fromisoformat(s["modified_time"]) if s.get("modified_time") else None
                ),
            )
            for s in data.get("sources", [])
        ]
        return cls(claims=claims, sources=sources, version=int(data.get("version", 1)))

    def content_fingerprint(self) -> str:
        """Stable hash of the substantive CV content for idempotent versioning.

        Excludes volatile fields (``ingested_at``, ``version``) so re-running ingestion
        with unchanged evidence produces the same fingerprint — and thus no new version.
        """
        material = {
            "claims": [
                {
                    "problem": c.problem,
                    "action": c.action,
                    "result": c.result,
                    "source_type": c.source_type,
                    "source_ref": c.source_ref,
                }
                for c in self.claims
            ],
            "sources": sorted(
                (
                    {
                        "source_type": s.source_type,
                        "external_ref": s.external_ref,
                        "title": s.title,
                        "mime_type": s.mime_type,
                        "raw_text": s.raw_text,
                        "modified_time": s.modified_time.isoformat() if s.modified_time else None,
                    }
                    for s in self.sources
                ),
                key=lambda d: (d["source_type"] or "", d["external_ref"] or ""),
            ),
        }
        blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredMasterCv:
    """A persisted Master CV version (what a repository returns)."""

    user_id: str
    version: int
    content_hash: str
    master_cv: MasterCv
    created_at: datetime | None = None


@runtime_checkable
class MasterCvRepository(Protocol):
    """Persistence for versioned Master CVs and their source provenance.

    ``save`` is **idempotent**: re-saving content whose fingerprint matches the latest
    version returns that version instead of creating a duplicate.
    """

    def save(self, user_id: str, master_cv: MasterCv) -> StoredMasterCv: ...

    def get_latest(self, user_id: str) -> StoredMasterCv | None: ...

    def get_version(self, user_id: str, version: int) -> StoredMasterCv | None: ...

    def list_versions(self, user_id: str) -> list[int]: ...


class MasterCvBuilder:
    """Builds a :class:`MasterCv` from provenance records via an injected structurer."""

    def __init__(self, structurer: ClaimStructurer | None = None) -> None:
        self._structurer = structurer or HeuristicClaimStructurer()

    def build(self, sources: Sequence[CvSource], version: int = 1) -> MasterCv:
        claims: list[ParClaim] = []
        for source in sources:
            claims.extend(self._structurer.structure(source))
        return MasterCv(claims=claims, sources=list(sources), version=version)
