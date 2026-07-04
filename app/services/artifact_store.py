"""In-memory :class:`ArtifactStore` — the mock-first default for dev and tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.domain.artifacts import Artifact, ArtifactStore


class InMemoryArtifactStore(ArtifactStore):
    """Dict-backed artifact rows, same upsert semantics as the SQL store."""

    def __init__(self) -> None:
        self._artifacts: dict[int, Artifact] = {}
        self._next_id = 1

    def record(
        self,
        user_id: str,
        kind: str,
        file_path: str,
        *,
        master_cv_version: int | None = None,
    ) -> Artifact:
        for artifact in self._artifacts.values():
            if (
                artifact.user_id == user_id
                and artifact.kind == kind
                and artifact.master_cv_version == master_cv_version
            ):
                updated = replace(artifact, file_path=file_path)
                self._artifacts[artifact.id] = updated
                return updated
        artifact = Artifact(
            id=self._next_id,
            user_id=user_id,
            kind=kind,
            file_path=file_path,
            master_cv_version=master_cv_version,
            created_at=datetime.now(tz=UTC),
        )
        self._artifacts[artifact.id] = artifact
        self._next_id += 1
        return artifact

    def get_latest(self, user_id: str, kind: str) -> Artifact | None:
        matches = [a for a in self._artifacts.values() if a.user_id == user_id and a.kind == kind]
        return max(matches, key=lambda a: (a.master_cv_version or 0, a.id), default=None)

    def list_artifacts(self, user_id: str, kind: str | None = None) -> list[Artifact]:
        return sorted(
            (
                a
                for a in self._artifacts.values()
                if a.user_id == user_id and (kind is None or a.kind == kind)
            ),
            key=lambda a: a.id,
        )
