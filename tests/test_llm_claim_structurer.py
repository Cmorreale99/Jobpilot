"""Offline tests for the LLM-backed PAR claim structurer.

Driven entirely by ``FakeLlmClient`` — no API key, no network. The focus is the
no-fabrication guarantee: claims whose evidence is not verbatim in the source are dropped
regardless of what the model returns.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.domain.cv import CvSource, MasterCvBuilder
from app.llm import FakeLlmClient, LlmClaimStructurer, ModelTier
from app.llm.types import LlmMessage

_SOURCE_TEXT = (
    "Senior Engineer, Acme Corp\n\n"
    "Reduced checkout latency by 40% by introducing a Redis cache layer, "
    "cutting p95 from 800ms to 480ms.\n\n"
    "Mentored three junior engineers through onboarding."
)


def _source(text: str = _SOURCE_TEXT) -> CvSource:
    return CvSource(
        source_type="gdrive",
        external_ref="drv_resume_001",
        title="Resume",
        mime_type="text/plain",
        raw_text=text,
        ingested_at=datetime.now(tz=UTC),
    )


def _response(claims: list[dict[str, object]]) -> str:
    return json.dumps({"claims": claims})


# --- happy path ------------------------------------------------------------------


def test_structures_par_claims_with_provenance() -> None:
    client = FakeLlmClient(
        [
            _response(
                [
                    {
                        "problem": "Slow checkout",
                        "action": "Introduced a Redis cache layer",
                        "result": "Cut p95 from 800ms to 480ms",
                        "evidence_text": "Reduced checkout latency by 40% by introducing a "
                        "Redis cache layer, cutting p95 from 800ms to 480ms.",
                    },
                    {
                        "problem": None,
                        "action": "Mentored three junior engineers",
                        "result": None,
                        "evidence_text": "Mentored three junior engineers through onboarding.",
                    },
                ]
            )
        ]
    )
    claims = LlmClaimStructurer(client).structure(_source())

    assert len(claims) == 2
    first = claims[0]
    assert first.action == "Introduced a Redis cache layer"
    assert first.problem == "Slow checkout"
    assert first.result == "Cut p95 from 800ms to 480ms"
    # Provenance is stamped from the source, not the model.
    assert first.source_ref == "drv_resume_001"
    assert first.source_type == "gdrive"
    # Optional fields absent in the source stay absent (not fabricated).
    assert claims[1].problem is None
    assert claims[1].result is None


def test_uses_bulk_tier_by_default() -> None:
    client = FakeLlmClient([_response([])])
    LlmClaimStructurer(client).structure(_source())
    assert client.calls[0].tier is ModelTier.BULK


def test_deep_tier_is_configurable() -> None:
    client = FakeLlmClient([_response([])])
    LlmClaimStructurer(client, tier=ModelTier.DEEP).structure(_source())
    assert client.calls[0].tier is ModelTier.DEEP


# --- no fabrication --------------------------------------------------------------


def test_drops_ungrounded_claim() -> None:
    """A hallucinated claim whose evidence is not in the source is discarded."""
    client = FakeLlmClient(
        [
            _response(
                [
                    {
                        "problem": None,
                        "action": "Led a $2M migration to Kubernetes",
                        "result": "Saved $500k annually",
                        "evidence_text": "Led a $2M migration to Kubernetes saving $500k a year.",
                    },
                    {
                        "problem": None,
                        "action": "Mentored three junior engineers",
                        "result": None,
                        "evidence_text": "Mentored three junior engineers through onboarding.",
                    },
                ]
            )
        ]
    )
    claims = LlmClaimStructurer(client).structure(_source())
    assert [c.action for c in claims] == ["Mentored three junior engineers"]


def test_grounding_tolerates_whitespace_reflow() -> None:
    """Verbatim quotes still match when the model reflows internal whitespace/case."""
    client = FakeLlmClient(
        [
            _response(
                [
                    {
                        "problem": None,
                        "action": "Mentored engineers",
                        "result": None,
                        # Extra spaces + different case vs. the source line.
                        "evidence_text": "mentored   three   junior   engineers  "
                        "through onboarding.",
                    }
                ]
            )
        ]
    )
    claims = LlmClaimStructurer(client).structure(_source())
    assert len(claims) == 1


def test_grounding_can_be_disabled() -> None:
    client = FakeLlmClient(
        [
            _response(
                [
                    {
                        "problem": None,
                        "action": "Invented achievement",
                        "result": None,
                        "evidence_text": "nowhere in the source",
                    }
                ]
            )
        ]
    )
    claims = LlmClaimStructurer(client, require_grounding=False).structure(_source())
    assert len(claims) == 1


# --- edges -----------------------------------------------------------------------


def test_blank_source_makes_no_llm_call() -> None:
    client = FakeLlmClient([_response([{"action": "x", "evidence_text": "y"}])])
    claims = LlmClaimStructurer(client).structure(_source("   \n  "))
    assert claims == []
    assert client.call_count == 0


def test_malformed_shape_retries_then_falls_back_to_heuristic() -> None:
    # Both attempts return the wrong shape (no "claims" key) -> after the corrective
    # retry, the source degrades to the heuristic structurer instead of raising
    # (one bad response must never sink the nightly ingestion — seen live).
    client = FakeLlmClient(['{"items": []}', '{"still": "wrong"}'])
    claims = LlmClaimStructurer(client).structure(_source())
    assert client.call_count == 2
    # The default _source() text has no PAR blocks/bullets, so the heuristic finds none.
    assert claims == []


def test_retry_recovers_after_bad_first_response() -> None:
    good = _response(
        [
            {
                "problem": None,
                "action": "Mentored three junior engineers",
                "result": None,
                "evidence_text": "Mentored three junior engineers through onboarding.",
            }
        ]
    )
    client = FakeLlmClient(["not json at all", good])
    claims = LlmClaimStructurer(client).structure(_source())
    assert len(claims) == 1
    assert client.call_count == 2


def test_prompt_includes_source_text_and_no_fabrication_rule() -> None:
    client = FakeLlmClient([_response([])])
    structurer = LlmClaimStructurer(client)
    structurer.structure(_source())
    call = client.calls[0]
    assert "Redis cache layer" in call.last_user_text  # source text is in the prompt
    assert call.system is not None and "Never infer, embellish, or invent" in call.system


# --- integration with the domain builder -----------------------------------------


def test_injects_into_master_cv_builder() -> None:
    client = FakeLlmClient(
        [
            _response(
                [
                    {
                        "problem": None,
                        "action": "Mentored three junior engineers",
                        "result": None,
                        "evidence_text": "Mentored three junior engineers through onboarding.",
                    }
                ]
            )
        ]
    )
    cv = MasterCvBuilder(structurer=LlmClaimStructurer(client)).build([_source()])
    assert len(cv.claims) == 1
    assert cv.claims[0].source_ref == "drv_resume_001"
    assert len(cv.sources) == 1


def test_structure_signature_matches_protocol() -> None:
    # Sanity: a plain user message list is what complete_json receives.
    client = FakeLlmClient([_response([])])
    LlmClaimStructurer(client).structure(_source())
    assert isinstance(client.calls[0].messages[0], LlmMessage)


def test_persistent_llm_failure_falls_back_to_heuristic_for_that_source() -> None:
    """A truncated/unparseable response (seen live on large PDFs) degrades one source
    to the heuristic structurer instead of sinking the whole ingestion."""
    from datetime import UTC, datetime

    from app.domain.cv import CvSource

    source = CvSource(
        source_type="gdrive",
        external_ref="doc-1",
        title="Write-up",
        mime_type="text/plain",
        raw_text=(
            "Problem: Settlement missed the cutoff.\n"
            "Action: Re-architected the pipeline into idempotent workers.\n"
            "Result: Cut runtime by 70%."
        ),
        ingested_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    client = FakeLlmClient(default_text='{"claims": [{"action": "truncated mid-str')
    claims = LlmClaimStructurer(client).structure(source)

    assert client.call_count == 2  # one attempt + one corrective retry, then fallback
    (claim,) = claims  # the heuristic parsed the PAR block instead
    assert claim.action == "Re-architected the pipeline into idempotent workers."
    assert claim.result == "Cut runtime by 70%."
