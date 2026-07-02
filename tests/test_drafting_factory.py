"""Drafting factory: flag off -> heuristics; on without a real client -> safe fallback."""

from __future__ import annotations

import logging

import pytest
from app.config import Settings
from app.domain.outreach import HeuristicOutreachDrafter
from app.domain.tailoring import HeuristicMaterialsTailorer
from app.llm.drafting import LlmMaterialsTailorer, LlmOutreachDrafter
from app.llm.fake import FakeLlmClient
from app.services.drafting_factory import create_drafters


def test_flag_off_returns_heuristics() -> None:
    tailorer, drafter = create_drafters(Settings(tailoring_llm_drafting=False))
    assert isinstance(tailorer, HeuristicMaterialsTailorer)
    assert isinstance(drafter, HeuristicOutreachDrafter)


def test_flag_on_without_llm_enabled_warns_and_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(tailoring_llm_drafting=True, llm_enabled=False)
    with caplog.at_level(logging.WARNING):
        tailorer, drafter = create_drafters(settings)
    assert isinstance(tailorer, HeuristicMaterialsTailorer)
    assert isinstance(drafter, HeuristicOutreachDrafter)
    assert any("TAILORING_LLM_DRAFTING" in r.message for r in caplog.records)


def test_flag_on_with_injected_client_returns_llm_drafters() -> None:
    settings = Settings(tailoring_llm_drafting=True, llm_enabled=False)
    tailorer, drafter = create_drafters(settings, llm_client=FakeLlmClient())
    assert isinstance(tailorer, LlmMaterialsTailorer)
    assert isinstance(drafter, LlmOutreachDrafter)
