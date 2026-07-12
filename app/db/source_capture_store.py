"""SQL :class:`SourceCaptureStore` over ``source_documents`` + ``source_document_versions``.

Canonical raw capture (H2, migration ``0017``). Version rows are immutable after
insert — ``raw_text``/``content_hash`` are never updated; only ``is_active`` moves,
keeping exactly one active version per document.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import SourceDocumentRow, SourceDocumentVersionRow, SourceElementRow
from app.domain.source_capture import (
    CapturedSourceDocument,
    CapturedSourceVersion,
    SourceElementInput,
    StoredSourceElement,
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
        structurer_version=row.structurer_version,
        ingestion_status=row.ingestion_status,
    )


def _to_element(row: SourceElementRow) -> StoredSourceElement:
    return StoredSourceElement(
        id=row.id,
        document_version_id=row.document_version_id,
        sequence_index=row.sequence_index,
        element_type=row.element_type,
        raw_start=row.raw_start,
        raw_end=row.raw_end,
        normalized_text=row.normalized_text,
        content_hash=row.content_hash,
        level=row.level,
        parent_id=row.parent_element_id,
        extraction_status=row.extraction_status,
        note=row.note,
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

    def list_documents(self, user_id: str) -> list[CapturedSourceDocument]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(SourceDocumentRow)
                .where(SourceDocumentRow.user_id == user_id)
                .order_by(SourceDocumentRow.id)
            )
            return [_to_document(row) for row in rows]

    def record_elements(
        self,
        version_id: int,
        elements: Sequence[SourceElementInput],
        *,
        structurer_version: int,
        ingestion_status: str,
    ) -> list[StoredSourceElement]:
        with self._session_factory() as session:
            version = session.get(SourceDocumentVersionRow, version_id)
            if version is None:
                raise LookupError(f"no source document version with id {version_id}")
            # Elements are a pure derivation of the immutable raw payload: replace
            # any prior derivation for this version (the raw row is never touched).
            session.execute(
                delete(SourceElementRow).where(SourceElementRow.document_version_id == version_id)
            )
            parent_ids: dict[int, int] = {}  # sequence_index -> stored id
            rows: list[SourceElementRow] = []
            for element in elements:
                row = SourceElementRow(
                    document_version_id=version_id,
                    sequence_index=element.sequence_index,
                    parent_element_id=(
                        parent_ids[element.parent_index]
                        if element.parent_index is not None
                        else None
                    ),
                    element_type=element.element_type,
                    level=element.level,
                    raw_start=element.raw_start,
                    raw_end=element.raw_end,
                    normalized_text=element.normalized_text,
                    content_hash=raw_content_hash(element.raw_text),
                    extraction_status=element.extraction_status,
                    note=element.note,
                )
                session.add(row)
                session.flush()
                parent_ids[element.sequence_index] = row.id
                rows.append(row)
            version.structurer_version = structurer_version
            version.ingestion_status = ingestion_status
            session.commit()
            return [_to_element(row) for row in rows]

    def list_elements(self, version_id: int) -> list[StoredSourceElement]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(SourceElementRow)
                .where(SourceElementRow.document_version_id == version_id)
                .order_by(SourceElementRow.sequence_index)
            )
            return [_to_element(row) for row in rows]

    def get_element(self, element_id: int) -> StoredSourceElement | None:
        with self._session_factory() as session:
            row = session.get(SourceElementRow, element_id)
            return _to_element(row) if row is not None else None

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
