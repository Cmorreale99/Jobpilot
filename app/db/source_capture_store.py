"""SQL :class:`SourceCaptureStore` over ``source_documents`` + ``source_document_versions``.

Canonical raw capture (H2, migration ``0017``). Version rows are immutable after
insert — ``raw_text``/``content_hash`` are never updated; only ``is_active`` moves,
keeping exactly one active version per document.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SourceDocumentRow, SourceDocumentVersionRow
from app.domain.source_capture import (
    CapturedSourceDocument,
    CapturedSourceVersion,
    raw_content_hash,
)
from app.domain.text_normalization import NORMALIZATION_VERSION


def _to_document(row: SourceDocumentRow) -> CapturedSourceDocument:
    return CapturedSourceDocument(
        id=row.id,
        user_id=row.user_id,
        source_type=row.source_type,
        source_ref=row.source_ref,
        title=row.title,
        mime_type=row.mime_type,
        modified_time=row.modified_time,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


def _to_version(row: SourceDocumentVersionRow) -> CapturedSourceVersion:
    return CapturedSourceVersion(
        id=row.id,
        document_id=row.document_id,
        content_hash=row.content_hash,
        raw_text=row.raw_text,
        extractor=row.extractor,
        normalization_version=row.normalization_version,
        is_active=row.is_active,
        fetched_at=row.fetched_at,
    )


class SqlSourceCaptureStore:
    """SQLAlchemy-backed capture store (see the protocol in ``domain/source_capture.py``)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

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
        with self._session_factory() as session:
            document = self._document_row(session, user_id, source_type, source_ref)
            if document is None:
                document = SourceDocumentRow(
                    user_id=user_id,
                    source_type=source_type,
                    source_ref=source_ref,
                    title=title,
                    mime_type=mime_type,
                    modified_time=modified_time,
                    size_bytes=size_bytes,
                )
                session.add(document)
                session.flush()
            else:
                document.title = title
                document.mime_type = mime_type
                document.modified_time = modified_time
                document.size_bytes = size_bytes

            content_hash = raw_content_hash(raw_text)
            versions = list(
                session.scalars(
                    select(SourceDocumentVersionRow).where(
                        SourceDocumentVersionRow.document_id == document.id
                    )
                )
            )
            match = next((v for v in versions if v.content_hash == content_hash), None)
            if match is None:
                match = SourceDocumentVersionRow(
                    document_id=document.id,
                    content_hash=content_hash,
                    raw_text=raw_text,
                    extractor=extractor,
                    normalization_version=NORMALIZATION_VERSION,
                    is_active=True,
                    fetched_at=datetime.now(tz=UTC),
                )
                session.add(match)
                session.flush()
            # Exactly one active version — the matched one; raw payloads untouched.
            for version in versions:
                if version.id != match.id and version.is_active:
                    version.is_active = False
            match.is_active = True
            session.commit()
            session.refresh(match)
            return _to_version(match)

    def get_active_version(
        self, user_id: str, source_type: str, source_ref: str
    ) -> CapturedSourceVersion | None:
        with self._session_factory() as session:
            document = self._document_row(session, user_id, source_type, source_ref)
            if document is None:
                return None
            row = session.scalar(
                select(SourceDocumentVersionRow).where(
                    SourceDocumentVersionRow.document_id == document.id,
                    SourceDocumentVersionRow.is_active.is_(True),
                )
            )
            return _to_version(row) if row is not None else None

    def list_versions(
        self, user_id: str, source_type: str, source_ref: str
    ) -> list[CapturedSourceVersion]:
        with self._session_factory() as session:
            document = self._document_row(session, user_id, source_type, source_ref)
            if document is None:
                return []
            rows = session.scalars(
                select(SourceDocumentVersionRow)
                .where(SourceDocumentVersionRow.document_id == document.id)
                .order_by(SourceDocumentVersionRow.id)
            )
            return [_to_version(row) for row in rows]

    def get_document(
        self, user_id: str, source_type: str, source_ref: str
    ) -> CapturedSourceDocument | None:
        with self._session_factory() as session:
            row = self._document_row(session, user_id, source_type, source_ref)
            return _to_document(row) if row is not None else None

    @staticmethod
    def _document_row(
        session: Session, user_id: str, source_type: str, source_ref: str
    ) -> SourceDocumentRow | None:
        return session.scalar(
            select(SourceDocumentRow).where(
                SourceDocumentRow.user_id == user_id,
                SourceDocumentRow.source_type == source_type,
                SourceDocumentRow.source_ref == source_ref,
            )
        )


__all__ = ["SqlSourceCaptureStore"]
