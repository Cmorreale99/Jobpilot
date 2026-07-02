"""Tests for the config-driven Master CV builder factory and its ingestion wiring.

All offline: the LLM path is exercised with a scripted ``FakeLlmClient``, never a real
client or network.
"""

from __future__ import annotations

import logging

import pytest
from app.config import Settings
from app.domain.cv import HeuristicClaimStructurer, MasterCvBuilder
from app.integrations.mock.drive import MockDriveClient
from app.llm import FakeLlmClient, LlmClaimStructurer
from app.llm.client import AnthropicClient
from app.services import cv_builder_factory
from app.services.cv_builder_factory import create_cv_builder
from app.services.master_cv_ingestion import build_master_cv_from_drive

# --- factory branches ------------------------------------------------------------


def test_default_uses_heuristic_structurer() -> None:
    builder = create_cv_builder(Settings())
    assert isinstance(builder, MasterCvBuilder)
    assert isinstance(builder._structurer, HeuristicClaimStructurer)


def test_flag_on_with_injected_client_uses_llm() -> None:
    fake = FakeLlmClient()
    builder = create_cv_builder(Settings(master_cv_llm_structuring=True), llm_client=fake)
    assert isinstance(builder._structurer, LlmClaimStructurer)
    assert builder._structurer._client is fake


def test_flag_on_without_real_client_falls_back_to_heuristic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=cv_builder_factory.__name__):
        builder = create_cv_builder(Settings(master_cv_llm_structuring=True, llm_enabled=False))
    assert isinstance(builder._structurer, HeuristicClaimStructurer)
    assert "falling back to the heuristic" in caplog.text.lower()


def test_flag_on_with_real_client_builds_anthropic_backed_structurer() -> None:
    builder = create_cv_builder(
        Settings(
            master_cv_llm_structuring=True,
            llm_enabled=True,
            anthropic_api_key="sk-test",
        )
    )
    assert isinstance(builder._structurer, LlmClaimStructurer)
    assert isinstance(builder._structurer._client, AnthropicClient)


# --- ingestion wiring ------------------------------------------------------------


async def test_ingestion_routes_through_llm_when_flag_on(
    mock_client: MockDriveClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the flag on, the default ingestion path builds via the LLM structurer."""
    fake = FakeLlmClient(handler=lambda messages, tier: '{"claims": []}')
    monkeypatch.setattr(cv_builder_factory, "create_llm_client", lambda *a, **k: fake)

    llm_settings = settings.model_copy(
        update={
            "master_cv_llm_structuring": True,
            "llm_enabled": True,
            "anthropic_api_key": "sk-test",
        }
    )
    cv = await build_master_cv_from_drive(mock_client, "user-1", llm_settings)

    # One structuring call per ingested Drive source; empty JSON -> no claims.
    assert fake.call_count == len(cv.sources) == 4
    assert cv.claims == []


async def test_ingestion_uses_heuristic_when_flag_off(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    cv = await build_master_cv_from_drive(mock_client, "user-1", settings)
    assert cv.claims, "heuristic structurer should still populate claims by default"
