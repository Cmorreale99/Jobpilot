"""In-memory :class:`ValidationRunLog` — the mock-first default for dev and tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from app.domain.validation_runs import ValidationRun, ValidationRunLog


class InMemoryValidationRunLog(ValidationRunLog):
    """Append-only list of validation runs, same semantics as the SQL log."""

    def __init__(self) -> None:
        self._runs: list[ValidationRun] = []

    def record(
        self,
        user_id: str,
        kind: str,
        subject_ref: str,
        *,
        passed: bool,
        detail: Sequence[str] = (),
    ) -> ValidationRun:
        run = ValidationRun(
            id=len(self._runs) + 1,
            user_id=user_id,
            kind=kind,
            subject_ref=subject_ref,
            passed=passed,
            detail=tuple(detail),
            created_at=datetime.now(tz=UTC),
        )
        self._runs.append(run)
        return run

    def list_runs(self, user_id: str, kind: str | None = None) -> list[ValidationRun]:
        return [
            run
            for run in self._runs
            if run.user_id == user_id and (kind is None or run.kind == kind)
        ]
