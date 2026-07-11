"""In-memory :class:`SourceCaptureStore` — the mock-first default for tests (H2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.domain.source_capture import (
    CapturedSourceDocument,
    CapturedSourceVersion,
    raw_content_hash,
)
from app.domain.text_normalization import NORMALIZATION_VERSION


class InMemorySourceCaptureStore:
    """Dict-backed capture store with the same lifecycle semantics as the SQL one."""

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str, str], CapturedSourceDocument] = {}
        self._versions: dict[int, list[CapturedSourceVersion]] = {}  # by document id
        self._next_document_id = 1
        self._next_version_id = 1

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
        key = (user_id, source_type, source_ref)
        document = self._documents.get(key)
        if document is None:
            document = CapturedSourceDocument(
                id=self._next_document_id,
                user_id=user_id,
                source_type=source_type,
                source_ref=source_ref,
                title=title,
                mime_type=mime_type,
                modified_time=modified_time,
                size_bytes=size_bytes,
                created_at=datetime.now(tz=UTC),
            )
            self._next_document_id += 1
        else:
            document = replace(
                document,
                title=title,
                mime_type=mime_type,
                modified_time=modified_time,
                size_bytes=size_bytes,
            )
        self._documents[key] = document

        content_hash = raw_content_hash(raw_text)
        versions = self._versions.setdefault(document.id, [])
        match = next((v for v in versions if v.content_hash == content_hash), None)
        if match is None:
            match = CapturedSourceVersion(
                id=self._next_version_id,
                document_id=document.id,
                content_hash=content_hash,
                raw_text=raw_text,
                extractor=extractor,
                normalization_version=NORMALIZATION_VERSION,
                fetched_at=datetime.now(tz=UTC),
            )
            self._next_version_id += 1
            versions.append(match)
        # Exactly one active version: the matched one (re-activated if content
        # returned to an earlier hash). Raw text/hash are never touched.
        self._versions[document.id] = [replace(v, is_active=(v.id == match.id)) for v in versions]
        return next(v for v in self._versions[document.id] if v.id == match.id)

    def get_active_version(
        self, user_id: str, source_type: str, source_ref: str
    ) -> CapturedSourceVersion | None:
        document = self._documents.get((user_id, source_type, source_ref))
        if document is None:
            return None
        return next((v for v in self._versions.get(document.id, []) if v.is_active), None)

    def list_versions(
        self, user_id: str, source_type: str, source_ref: str
    ) -> list[CapturedSourceVersion]:
        document = self._documents.get((user_id, source_type, source_ref))
        if document is None:
            return []
        return sorted(self._versions.get(document.id, []), key=lambda v: v.id)

    def get_document(
        self, user_id: str, source_type: str, source_ref: str
    ) -> CapturedSourceDocument | None:
        return self._documents.get((user_id, source_type, source_ref))


__all__ = ["InMemorySourceCaptureStore"]
