"""Local-directory :class:`UploadsClient` — user-supplied career artifacts.

Uploads are files the user places in a configured folder (``UPLOADS_DIR``): an exported
resume, a project write-up, an award letter. Unlike Drive/GitHub there is no remote
service, so the one implementation is real *and* offline — tests point it at
``tests/fixtures/uploads/``.

Only text-bearing files are ever read; the suffix allowlist is applied by the service
layer (``apply_upload_policy``), mirroring how Drive's MIME policy works.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.integrations.base import (
    UploadCandidate,
    UploadDocument,
    UploadsClient,
    UploadsConfigurationError,
)

# Suffix → MIME for the formats the local client can extract text from.
_SUFFIX_MIME = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
_FALLBACK_MIME = "application/octet-stream"


class LocalUploadsClient:
    """Serves career artifacts from a local directory (non-recursive)."""

    def __init__(self, uploads_dir: str | Path) -> None:
        self._dir = Path(uploads_dir)

    def _require_dir(self) -> Path:
        if not self._dir.is_dir():
            raise UploadsConfigurationError(
                f"Uploads directory not found at {self._dir}. Create it or unset UPLOADS_DIR."
            )
        return self._dir

    async def list_candidate_uploads(self) -> list[UploadCandidate]:
        directory = self._require_dir()
        candidates = []
        for path in sorted(p for p in directory.iterdir() if p.is_file()):
            candidates.append(
                UploadCandidate(
                    upload_ref=path.name,
                    title=path.stem.replace("_", " "),
                    mime_type=_SUFFIX_MIME.get(path.suffix.lower(), _FALLBACK_MIME),
                    modified_time=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                )
            )
        return candidates

    async def read_upload(self, upload_ref: str) -> UploadDocument:
        path = self._require_dir() / upload_ref
        if not path.is_file():
            raise FileNotFoundError(f"No uploaded file named {upload_ref!r}.")
        return UploadDocument(
            upload_ref=upload_ref,
            title=path.stem.replace("_", " "),
            mime_type=_SUFFIX_MIME.get(path.suffix.lower(), _FALLBACK_MIME),
            text=path.read_text(encoding="utf-8"),
            modified_time=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )


def create_uploads_client(settings: Settings) -> UploadsClient | None:
    """Return the local uploads client, or ``None`` when no directory is configured.

    An empty ``UPLOADS_DIR`` (the default) means the feature is unused — ingestion
    simply skips uploads rather than failing.
    """
    if not settings.uploads_dir.strip():
        return None
    return LocalUploadsClient(settings.uploads_dir)
