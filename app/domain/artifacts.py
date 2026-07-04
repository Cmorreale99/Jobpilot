"""Rendered artifacts: generated files (the Master CV docx) tracked in the database.

V2 populates a single kind — ``master_cv_docx``, the rendered view of one approved-
claims Master CV version. V3's tailored per-job resumes land in the same table with
new kinds. The file itself lives on disk under the configured artifacts directory;
the row is the pointer plus provenance (which version it renders).

Pure contracts only; implementations live in ``services/`` (in-memory) and ``db/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

KIND_MASTER_CV_DOCX = "master_cv_docx"


@dataclass(frozen=True)
class Artifact:
    """One generated file: what it is, where it lives, and which version it renders."""

    id: int
    user_id: str
    kind: str
    file_path: str
    master_cv_version: int | None = None
    created_at: datetime | None = None


@runtime_checkable
class ArtifactStore(Protocol):
    """Persistence for artifact rows.

    ``record`` upserts on ``(user_id, kind, master_cv_version)`` — re-rendering the
    same version refreshes the row (and the file on disk) instead of duplicating it.
    """

    def record(
        self,
        user_id: str,
        kind: str,
        file_path: str,
        *,
        master_cv_version: int | None = None,
    ) -> Artifact: ...

    def get_latest(self, user_id: str, kind: str) -> Artifact | None: ...

    def list_artifacts(self, user_id: str, kind: str | None = None) -> list[Artifact]: ...
