"""Factory wiring for the claim extractor: safe default, flag, footgun guard."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.domain.claims import HeuristicTwoPassExtractor
from app.llm.extraction import LlmTwoPassExtractor
from app.llm.fake import FakeLlmClient
from app.services.extractor_factory import create_claim_extractor


def test_default_is_the_deterministic_heuristic() -> None:
    extractor = create_claim_extractor(Settings())
    assert isinstance(extractor, HeuristicTwoPassExtractor)


def test_flag_without_real_client_falls_back_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(claims_llm_extraction=True, llm_enabled=False)
    with caplog.at_level("WARNING"):
        extractor = create_claim_extractor(settings)
    assert isinstance(extractor, HeuristicTwoPassExtractor)
    assert "CLAIMS_LLM_EXTRACTION" in caplog.text


def test_flag_with_injected_client_selects_the_llm_extractor() -> None:
    settings = Settings(claims_llm_extraction=True, llm_enabled=False)
    extractor = create_claim_extractor(settings, llm_client=FakeLlmClient())
    assert isinstance(extractor, LlmTwoPassExtractor)
