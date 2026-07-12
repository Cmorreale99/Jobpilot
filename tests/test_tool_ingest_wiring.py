"""The CLI ingest tools must wire H2 capture + validation logging.

``python -m app.tools.run_roster_detection`` and ``run_claim_extraction`` are the
CLI twins of ``POST /roster/detect`` / ``/roster/assign``. The API path passes a
``capture_store`` (raw source text persisted before normalization, H2) and a
``validation_log`` (the gather/reconciliation passes land in ``validation_runs``);
a CLI ingest that skips them silently produces sources the pipeline audit's
``capture`` check fails on. These tests pin the wiring, not the ingest itself.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from app.config import Settings
from app.db.source_capture_store import SqlSourceCaptureStore
from app.db.validation_run_log import SqlValidationRunLog
from app.services.roster import RosterAssignmentReport, RosterDetectionReport
from app.tools import run_claim_extraction as extraction_tool
from app.tools import run_roster_detection as detection_tool


@pytest.fixture
def tool_settings(settings: Settings, tmp_path: Any) -> Settings:
    """The conftest safe-path settings, pointed at a throwaway SQLite file."""
    return settings.model_copy(
        update={"database_url": f"sqlite+pysqlite:///{tmp_path / 'tool.db'}"}
    )


def test_detection_tool_wires_capture_and_log(
    monkeypatch: pytest.MonkeyPatch, tool_settings: Settings
) -> None:
    captured: dict[str, Any] = {}

    async def fake_detection(*args: Any, **kwargs: Any) -> RosterDetectionReport:
        captured.update(kwargs)
        return RosterDetectionReport(documents=0)

    monkeypatch.setattr(detection_tool, "get_settings", lambda: tool_settings)
    monkeypatch.setattr(detection_tool, "run_roster_detection", fake_detection)

    asyncio.run(detection_tool._run())

    assert isinstance(captured.get("capture_store"), SqlSourceCaptureStore)
    assert isinstance(captured.get("validation_log"), SqlValidationRunLog)


def test_extraction_tool_wires_capture_and_log(
    monkeypatch: pytest.MonkeyPatch, tool_settings: Settings
) -> None:
    assignment_kwargs: dict[str, Any] = {}

    async def fake_assignment(*args: Any, **kwargs: Any) -> RosterAssignmentReport:
        assignment_kwargs.update(kwargs)
        return RosterAssignmentReport(chunks=0, assigned=0, unassigned=0)

    async def fake_extraction(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            claims=[],
            flagged=[],
            missing_results=[],
            dropped=[],
            deduped=[],
            failed_groups=[],
            skipped_unchanged=[],
        )

    monkeypatch.setattr(extraction_tool, "get_settings", lambda: tool_settings)
    monkeypatch.setattr(extraction_tool, "run_roster_assignment", fake_assignment)
    monkeypatch.setattr(extraction_tool, "run_claim_extraction", fake_extraction)

    asyncio.run(extraction_tool._run())

    assert isinstance(assignment_kwargs.get("capture_store"), SqlSourceCaptureStore)
    assert isinstance(assignment_kwargs.get("validation_log"), SqlValidationRunLog)
