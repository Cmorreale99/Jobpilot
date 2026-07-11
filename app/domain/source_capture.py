"""Canonical source capture — the immutable raw layer (hardening H2).

Point of no return #1 done right: every gathered source's **as-received** text is
durable *before* normalization touches it, so every downstream transform (normalize →
chunk → assign → extract) is a recomputable derivation of a stored original instead of
the only copy. Two shapes:

* ``CapturedSourceDocument`` — the source's identity (``user_id, source_type,
  source_ref``) plus its latest metadata (title/MIME/modified/size).
* ``CapturedSourceVersion`` — one immutable raw payload per distinct content hash.
  ``raw_text``/``content_hash`` are never mutated after insert; lifecycle lives in
  ``is_active`` (exactly one active version per document). Content returning to an
  earlier hash re-activates that version rather than duplicating the payload.

Pure contracts only; implementations live in ``services/source_capture.py``
(in-memory) and ``db/source_capture_store.py`` (SQL, migration ``0017``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Protocol, runtime_checkable


def raw_content_hash(raw_text: str) -> str:
    """The version identity of a raw payload: sha256 over its exact bytes (UTF-8)."""
    return sha256(raw_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapturedSourceDocument:
    """One captured source's identity + latest-known metadata."""

    id: int
    user_id: str
    source_type: str
    source_ref: str
    title: str
    mime_type: str | None = None
    modified_time: datetime | None = None
    size_bytes: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class CapturedSourceVersion:
    """One immutable raw payload of a captured source.

    ``raw_text`` is the text exactly as the client returned it — pre-normalization,
    pre-chunking. ``extractor`` records what produced it (e.g. ``drive:McpDriveClient``)
    so a payload is auditable back to its extraction path.
    """

    id: int
    document_id: int
    content_hash: str
    raw_text: str
    extractor: str
    normalization_version: int
    is_active: bool = True
    fetched_at: datetime | None = None


@runtime_checkable
class SourceCaptureStore(Protocol):
    """Append-only store of raw source payloads with an active-version lifecycle."""

    def capture(
        self,
        user_id: str,
        *,
        source_type: str,
        source_ref: str,
        title: str,
        raw_text: str,
        extractor: str,
        mime_type: str | None = None,
        modified_time: datetime | None = None,
        size_bytes: int | None = None,
    ) -> CapturedSourceVersion:
        """Record one gathered payload; idempotent by content hash.

        Upserts the document identity (metadata refreshed), then: an existing version
        with this payload's hash becomes/stays the single active one; a new hash
        inserts a new active version and deactivates the rest. Raw text and hash are
        never mutated — only the ``is_active`` flag moves.
        """
        ...

    def get_active_version(
        self, user_id: str, source_type: str, source_ref: str
    ) -> CapturedSourceVersion | None:
        """The active raw payload for one source, or ``None`` if never captured."""
        ...

    def list_versions(
        self, user_id: str, source_type: str, source_ref: str
    ) -> list[CapturedSourceVersion]:
        """Every captured version of one source, oldest first (the full raw history)."""
        ...

    def get_document(
        self, user_id: str, source_type: str, source_ref: str
    ) -> CapturedSourceDocument | None:
        """The captured document identity/metadata, or ``None`` if never captured."""
        ...


__all__ = [
    "CapturedSourceDocument",
    "CapturedSourceVersion",
    "SourceCaptureStore",
    "raw_content_hash",
]
