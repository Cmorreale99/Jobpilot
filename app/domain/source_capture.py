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

from collections.abc import Sequence
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
    so a payload is auditable back to its extraction path. ``structurer_version`` /
    ``ingestion_status`` record the element derivation (H4): which structurer
    generation produced this version's element tree and whether it reconciled
    (``ok``) or left characters unaccounted (``failed``) — ``None`` before any
    structuring pass.
    """

    id: int
    document_id: int
    content_hash: str
    raw_text: str
    extractor: str
    normalization_version: int
    is_active: bool = True
    fetched_at: datetime | None = None
    structurer_version: int | None = None
    ingestion_status: str | None = None


# Version-level reconciliation statuses (H4): did the element tree account for every
# character of the raw text?
INGESTION_OK = "ok"
INGESTION_FAILED = "failed"


@dataclass(frozen=True)
class StoredSourceElement:
    """One persisted structural element of a captured version (H4).

    Mirrors :class:`app.domain.source_structure.SourceElement` plus persistence
    identity: ``parent_id`` is the stored id of the governing element (resolved from
    the domain element's ``parent_index`` at write time), ``normalized_text`` is the
    normalizer's view of the element's raw slice, and ``extraction_status`` is the
    element's explicit disposition (``ok|unsupported|parser_error``).
    """

    id: int
    document_version_id: int
    sequence_index: int
    element_type: str
    raw_start: int
    raw_end: int
    normalized_text: str
    content_hash: str
    level: int | None = None
    parent_id: int | None = None
    extraction_status: str = "ok"
    note: str | None = None


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

    def record_elements(
        self,
        version_id: int,
        elements: Sequence[SourceElementInput],
        *,
        structurer_version: int,
        ingestion_status: str,
    ) -> list[StoredSourceElement]:
        """Persist a version's element tree (H4), replacing any prior derivation.

        Elements are a pure derivation of the immutable raw payload, so re-recording
        (e.g. after a ``STRUCTURER_VERSION`` bump) replaces the version's rows —
        the raw payload itself is never touched. Stamps the version row with the
        structurer generation and its reconciliation status (``ok``/``failed``).
        Parent links are resolved from each input's ``parent_index`` to stored ids.
        """
        ...

    def list_elements(self, version_id: int) -> list[StoredSourceElement]:
        """A version's element tree in document order (``sequence_index``)."""
        ...


@dataclass(frozen=True)
class SourceElementInput:
    """One element as handed to :meth:`SourceCaptureStore.record_elements`.

    The persistence-agnostic shape: structural fields plus the pre-computed
    ``normalized_text`` view; the store adds ids and resolves ``parent_index``.
    """

    sequence_index: int
    element_type: str
    raw_start: int
    raw_end: int
    raw_text: str
    normalized_text: str
    level: int | None = None
    parent_index: int | None = None
    extraction_status: str = "ok"
    note: str | None = None


__all__ = [
    "INGESTION_FAILED",
    "INGESTION_OK",
    "CapturedSourceDocument",
    "CapturedSourceVersion",
    "SourceCaptureStore",
    "SourceElementInput",
    "StoredSourceElement",
    "raw_content_hash",
]
