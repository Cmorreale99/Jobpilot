"""LLM prep packets: DEEP tier, grounded prompt, heuristic fallback, factory guard."""

from __future__ import annotations

import json
import logging

import pytest
from app.config import Settings
from app.domain.cv import MasterCv, ParClaim
from app.domain.interviews import (
    HeuristicPrepPacketGenerator,
    Interview,
    InterviewStage,
)
from app.llm.fake import FakeLlmClient
from app.llm.prep import LlmPrepPacketGenerator
from app.llm.types import ModelTier
from app.services.prep_factory import create_prep_generator

_INTERVIEW = Interview(
    id=7,
    user_id="u1",
    source_message_id="m1",
    company="Ledgerline",
    job_title="Staff Backend Engineer",
    stage=InterviewStage.DETECTED,
)

_CV = MasterCv(
    claims=[
        ParClaim(
            action="Re-architected the settlement pipeline over Kafka",
            source_ref="resume",
            result="Cut runtime by 70%",
        )
    ]
)


def test_llm_packet_happy_path() -> None:
    client = FakeLlmClient([json.dumps({"content": "# Prep\nLead with the Kafka story."})])
    packet = LlmPrepPacketGenerator(client).generate(_INTERVIEW, _CV, None)
    assert packet.interview_id == 7
    assert packet.content.startswith("# Prep")
    call = client.calls[0]
    assert call.tier is ModelTier.DEEP
    # Prompt carries only real evidence.
    assert "Re-architected the settlement pipeline over Kafka" in call.last_user_text
    assert "Ledgerline" in call.last_user_text


def test_llm_packet_falls_back_on_persistent_failure() -> None:
    client = FakeLlmClient(default_text="not json")
    packet = LlmPrepPacketGenerator(client).generate(_INTERVIEW, _CV, None)
    assert packet == HeuristicPrepPacketGenerator().generate(_INTERVIEW, _CV, None)


def test_factory_flag_off_returns_heuristic() -> None:
    assert isinstance(
        create_prep_generator(Settings(interview_llm_prep=False)), HeuristicPrepPacketGenerator
    )


def test_factory_flag_on_without_llm_warns_and_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        generator = create_prep_generator(Settings(interview_llm_prep=True, llm_enabled=False))
    assert isinstance(generator, HeuristicPrepPacketGenerator)
    assert any("INTERVIEW_LLM_PREP" in r.message for r in caplog.records)


def test_factory_flag_on_with_injected_client() -> None:
    generator = create_prep_generator(Settings(interview_llm_prep=True), llm_client=FakeLlmClient())
    assert isinstance(generator, LlmPrepPacketGenerator)
