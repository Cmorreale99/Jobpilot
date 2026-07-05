"""Offline tests for the LLM-backed two-pass extractor.

Driven by ``FakeLlmClient`` — no API key, no network. The focus is grounding: every
quote the model returns must exist verbatim in the cited chunk, or the claim/result
is dropped rather than trusted. Results are never invented, only attached.
"""

from __future__ import annotations

import json

from app.domain.claims import (
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    ClaimField,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    ResultKind,
)
from app.llm.extraction import LlmTwoPassExtractor
from app.llm.fake import FakeLlmClient
from app.llm.types import ModelTier

_README = EvidenceChunk(
    SOURCE_GITHUB_README,
    "jordanrivera/carrier-etl",
    "# carrier-etl\n\nBuilt a Kafka consumer in Python for carrier feeds.",
)
_COMMIT = EvidenceChunk(
    SOURCE_GITHUB_COMMIT,
    "c002",
    "Cut ingestion failure rate from 12% to 0.5% across the scoring jobs.",
)
_GROUP = EvidenceGroup(
    experience=ExperienceSeed(name="carrier-etl", section=ExperienceSection.PROJECTS_HACKATHONS),
    chunks=(_README, _COMMIT),
)


def _pass1(claims: list[dict[str, object]]) -> str:
    return json.dumps({"claims": claims})


def _pass2(results: list[dict[str, object]]) -> str:
    return json.dumps({"results": results})


_GROUNDED_PASS1 = _pass1(
    [
        {
            "chunk_index": 0,
            "action": "Built a Kafka consumer in Python for carrier feeds",
            "tools": ["Kafka", "Python"],
            "action_quote": "Built a Kafka consumer in Python for carrier feeds.",
            "problem": None,
        }
    ]
)


def test_two_passes_assemble_a_grounded_claim() -> None:
    client = FakeLlmClient(
        [
            _GROUNDED_PASS1,
            _pass2(
                [
                    {
                        "claim_index": 0,
                        "chunk_index": 1,
                        "outcome_quote": (
                            "Cut ingestion failure rate from 12% to 0.5% across the scoring jobs."
                        ),
                        "result_text": "Cut ingestion failure rate from 12% to 0.5%",
                        "result_kind": "quantified",
                        "metric_text": "Cut ingestion failure rate from 12% to 0.5%",
                        "resolves": "quality",
                    }
                ]
            ),
        ]
    )
    claims = LlmTwoPassExtractor(client).extract(_GROUP)

    assert client.call_count == 2
    assert all(call.tier is ModelTier.BULK for call in client.calls)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.action_tools == ("Kafka", "Python")
    assert claim.result_kind is ResultKind.QUANTIFIED
    assert claim.result_metric_json == {
        "resolves": "quality",
        "metric_text": "Cut ingestion failure rate from 12% to 0.5%",
    }
    result_refs = [ref for ref in claim.evidence if ref.field is ClaimField.RESULT]
    assert len(result_refs) == 1
    assert result_refs[0].chunk is _COMMIT


def test_ungrounded_outcome_quote_leaves_result_missing() -> None:
    client = FakeLlmClient(
        [
            _GROUNDED_PASS1,
            _pass2(
                [
                    {
                        "claim_index": 0,
                        "chunk_index": 1,
                        "outcome_quote": "Cut failure rate to zero forever.",
                        "result_text": "Cut failure rate to zero forever.",
                        "result_kind": "quantified",
                        "metric_text": "Cut failure rate to zero forever.",
                        "resolves": "quality",
                    }
                ]
            ),
        ]
    )
    claims = LlmTwoPassExtractor(client).extract(_GROUP)

    assert len(claims) == 1
    assert claims[0].result_kind is ResultKind.MISSING
    assert claims[0].result_text is None
    assert claims[0].result_metric_json is None


def test_ungrounded_action_quote_drops_the_claim_and_skips_pass2() -> None:
    client = FakeLlmClient(
        [
            _pass1(
                [
                    {
                        "chunk_index": 0,
                        "action": "Built a Spark job",
                        "tools": ["Spark"],
                        "action_quote": "Built a Spark job for the warehouse.",
                        "problem": None,
                    }
                ]
            )
        ]
    )
    claims = LlmTwoPassExtractor(client).extract(_GROUP)

    assert claims == []
    assert client.call_count == 1  # nothing grounded -> no pass 2


def test_ungrounded_problem_is_stripped_but_claim_survives() -> None:
    client = FakeLlmClient(
        [
            _pass1(
                [
                    {
                        "chunk_index": 0,
                        "action": "Built a Kafka consumer in Python for carrier feeds",
                        "tools": ["Kafka", "Python"],
                        "action_quote": "Built a Kafka consumer in Python for carrier feeds.",
                        "problem": {
                            "text": "Feeds were failing nightly",
                            "quote": "Feeds were failing nightly and paging ops.",
                            "chunk_index": 0,
                            "cost_dimension": "quality",
                            "inefficiency": "not_working",
                        },
                    }
                ]
            ),
            _pass2([]),
        ]
    )
    claims = LlmTwoPassExtractor(client).extract(_GROUP)

    assert len(claims) == 1
    assert claims[0].problem_text is None
    assert claims[0].problem_cost_dimension is None


def test_llm_failure_raises_loudly_instead_of_falling_back() -> None:
    import pytest
    from app.domain.claims import ClaimExtractionError

    client = FakeLlmClient(["not json", "still not json"])  # initial + parse retry
    with pytest.raises(ClaimExtractionError, match="carrier-etl"):
        LlmTwoPassExtractor(client).extract(_GROUP)


def test_oversized_groups_split_into_batches() -> None:
    """A commit-heavy group extracts in multiple small calls, not one giant prompt."""
    from app.llm import extraction as extraction_module

    chunks = tuple(
        EvidenceChunk(SOURCE_GITHUB_COMMIT, f"o/r@c{i}", f"Fixed ingest bug number {i} in Python")
        for i in range(70)
    )
    group = EvidenceGroup(
        experience=ExperienceSeed(name="big-repo", section=ExperienceSection.PROJECTS_HACKATHONS),
        chunks=chunks,
    )
    batches = extraction_module._split_group(group)
    assert len(batches) == 3  # 70 chunks under the 30-chunk budget
    assert [len(b.chunks) for b in batches] == [30, 30, 10]
    assert tuple(c for b in batches for c in b.chunks) == chunks  # nothing lost/reordered

    # Two passes per batch: 6 calls total, drafts concatenated across batches.
    responses = []
    for batch in batches:
        quote = batch.chunks[0].chunk_text
        responses.append(
            _pass1(
                [
                    {
                        "chunk_index": 0,
                        "action": quote,
                        "tools": ["Python"],
                        "action_quote": quote,
                        "problem": None,
                    }
                ]
            )
        )
        responses.append(_pass2([]))
    client = FakeLlmClient(responses)
    drafts = LlmTwoPassExtractor(client).extract(group)
    assert len(client.calls) == 6
    assert [d.action_text for d in drafts] == [
        "Fixed ingest bug number 0 in Python",
        "Fixed ingest bug number 30 in Python",
        "Fixed ingest bug number 60 in Python",
    ]
    # Every citation resolves to a real chunk of the ORIGINAL group.
    group_refs = {c.source_ref for c in chunks}
    assert all(ref.chunk.source_ref in group_refs for d in drafts for ref in d.evidence)


def test_char_budget_also_splits() -> None:
    from app.llm import extraction as extraction_module

    big = "x" * 20_000
    group = EvidenceGroup(
        experience=ExperienceSeed(name="r", section=ExperienceSection.PROJECTS_HACKATHONS),
        chunks=(
            EvidenceChunk(SOURCE_GITHUB_README, "o/r#chars=0-1", big),
            EvidenceChunk(SOURCE_GITHUB_README, "o/r#chars=1-2", big),
        ),
    )
    assert len(extraction_module._split_group(group)) == 2


def test_bounced_violations_are_folded_into_the_prompts() -> None:
    client = FakeLlmClient([_GROUNDED_PASS1, _pass2([])])
    LlmTwoPassExtractor(client).extract(
        _GROUP, violations=["problem_missing: claim declares no Problem"]
    )
    for call in client.calls:
        assert "problem_missing" in call.last_user_text
