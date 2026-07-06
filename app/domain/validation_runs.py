"""Validation-run logging: one auditable row per deterministic verification.

V2 has two verification surfaces sharing one philosophy — the PAR validator gating
extracted claims and the interview provenance check gating inbox detections. Every run
of either is recorded here (pass or fail, with the specific findings), so "why was this
rejected?" and "was this ever verified?" are answerable from the database. V3's
NLI/LLM-judge rows land in the same table.

Pure contracts only; implementations live in ``services/`` (in-memory) and ``db/``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

KIND_PAR_VALIDATION = "par_validation"
KIND_INTERVIEW_VERIFICATION = "interview_verification"
# One row per evidence group whose LLM extraction failed outright (no silent fallback).
KIND_EXTRACTION_FAILURE = "extraction_failure"
# One scorecard row per extraction run: the slop metrics (audit Phase 4).
KIND_EXTRACTION_EVAL = "extraction_eval"
# One row per cross-entity overlap pass: merge prompts found (V3 Phase 1, §3.7).
KIND_ENTITY_OVERLAP = "entity_overlap"
# One row per confirmed entity synthesized or quarantined in a story-synthesis run
# (V3 Phase 3): pass = a clean draft promoted to review; fail = fatal structural findings.
KIND_STORY_SYNTHESIS = "story_synthesis"


@dataclass(frozen=True)
class ValidationRun:
    """One recorded verification: what was checked, whether it passed, and why not."""

    id: int
    user_id: str
    kind: str
    subject_ref: str
    passed: bool
    detail: tuple[str, ...] = ()
    created_at: datetime | None = None


@runtime_checkable
class ValidationRunLog(Protocol):
    """Append-only log of validation runs."""

    def record(
        self,
        user_id: str,
        kind: str,
        subject_ref: str,
        *,
        passed: bool,
        detail: Sequence[str] = (),
    ) -> ValidationRun: ...

    def list_runs(self, user_id: str, kind: str | None = None) -> list[ValidationRun]: ...
