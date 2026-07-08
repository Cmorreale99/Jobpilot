"""LLM-backed tailoring + outreach drafting against the fake client.

What matters here is the grounding and the degradation: highlight ids resolve to real
claims (hallucinated ids dropped), and any LLM failure falls back to the deterministic
heuristics instead of crashing or losing the application.
"""

from __future__ import annotations

import json

from app.domain.cv import MasterCv, ParClaim
from app.domain.jobs import Job, JobMatch
from app.domain.outreach import Contact, HeuristicOutreachDrafter
from app.domain.tailoring import HeuristicMaterialsTailorer, TailoredMaterials
from app.llm.drafting import LlmMaterialsTailorer, LlmOutreachDrafter
from app.llm.fake import FakeLlmClient
from app.llm.types import ModelTier

_CLAIMS = [
    ParClaim(
        action="Re-architected the settlement pipeline over Kafka",
        source_ref="resume",
        result="Cut runtime by 70%",
    ),
    ParClaim(action="Introduced distributed tracing", source_ref="resume"),
]

_JOB = Job(
    source="mock",
    external_id="j-pay",
    title="Staff Backend Engineer, Payments",
    company="Ledgerline",
    description="Settlement workers over Kafka.",
)

_MATCH = JobMatch(job=_JOB, score=0.9, rank=1, rationale="fits", matched_terms=("kafka",))

_MATERIALS = TailoredMaterials(
    summary="Fit summary.", highlights=("Highlight one",), cover_letter="(letter)"
)


def _cv() -> MasterCv:
    return MasterCv(claims=list(_CLAIMS), version=2)


def _tailor_reply(ids: list[str]) -> str:
    return json.dumps(
        {"summary": "Strong payments fit.", "highlight_ids": ids, "cover_letter": "Dear team, …"}
    )


# --- tailorer ----------------------------------------------------------------------


def test_tailorer_resolves_highlights_from_real_claims() -> None:
    client = FakeLlmClient([_tailor_reply(["c0"])])
    materials = LlmMaterialsTailorer(client).tailor(_cv(), _MATCH)
    assert materials.summary == "Strong payments fit."
    assert materials.highlights == (
        "Re-architected the settlement pipeline over Kafka — Cut runtime by 70%",
    )
    assert client.calls[0].tier is ModelTier.DEEP


def test_tailorer_puts_claims_block_in_cached_context_not_the_prompt() -> None:
    # The numbered-claims block is the same for every application tailored in a run —
    # it rides in cached_context so the top-N calls share one prompt-cache entry.
    client = FakeLlmClient([_tailor_reply(["c0"])])
    LlmMaterialsTailorer(client).tailor(_cv(), _MATCH)
    call = client.calls[0]
    assert call.cached_context is not None
    assert "id=c0" in call.cached_context
    assert "settlement pipeline" in call.cached_context
    assert "id=c0" not in call.last_user_text
    assert "Ledgerline" in call.last_user_text  # only the posting varies per call


def test_tailorer_drops_hallucinated_and_duplicate_ids() -> None:
    client = FakeLlmClient([_tailor_reply(["c9", "c1", "c1"])])
    materials = LlmMaterialsTailorer(client).tailor(_cv(), _MATCH)
    assert materials.highlights == ("Introduced distributed tracing",)


def test_tailorer_with_no_valid_ids_falls_back_to_heuristic() -> None:
    client = FakeLlmClient([_tailor_reply(["c9"])])
    materials = LlmMaterialsTailorer(client).tailor(_cv(), _MATCH)
    assert materials == HeuristicMaterialsTailorer().tailor(_cv(), _MATCH)


def test_invented_numbers_fail_closed_to_the_heuristic() -> None:
    """Audit §9 tailoring safety: a number in no referenced claim never leaves."""
    reply = json.dumps(
        {
            "summary": "Delivered 85% cost reduction at scale.",  # 85 is invented
            "highlight_ids": ["c0"],
            "cover_letter": "Dear team, …",
        }
    )
    client = FakeLlmClient([reply])
    materials = LlmMaterialsTailorer(client).tailor(_cv(), _MATCH)
    assert materials == HeuristicMaterialsTailorer().tailor(_cv(), _MATCH)
    assert "85" not in materials.summary


def test_supported_numbers_pass_and_highlights_carry_claim_ids() -> None:
    ledger_cv = MasterCv(
        claims=[
            ParClaim(
                action="Re-architected the settlement pipeline over Kafka",
                result="Cut runtime by 70%",
                source_type="approved_claim",
                source_ref="claim:41",
            )
        ],
        version=3,
    )
    reply = json.dumps(
        {
            "summary": "Cut settlement runtime by 70%.",  # 70 comes from the claim
            "highlight_ids": ["c0"],
            "cover_letter": "Dear team, …",
        }
    )
    materials = LlmMaterialsTailorer(FakeLlmClient([reply])).tailor(ledger_cv, _MATCH)
    assert materials.summary == "Cut settlement runtime by 70%."
    assert materials.highlight_claim_ids == (41,)  # traceable to the approved claim


def test_tailorer_grounds_story_numbers_against_cited_evidence() -> None:
    """§6: story component text may be composed, so numbers ground against evidence_text."""
    story_cv = MasterCv(
        claims=[
            ParClaim(
                action="Re-architected the settlement pipeline over Kafka",
                result="Cut runtime dramatically",  # composed text carries NO number
                source_type="approved_story",
                source_ref="story:3:r-abc123",
                evidence_text="Cut settlement batch runtime by 70% after the migration",
            )
        ],
        version=4,
    )
    reply = json.dumps(
        {
            "summary": "Cut settlement runtime by 70%.",  # 70 is in the cited evidence
            "highlight_ids": ["c0"],
            "cover_letter": "Dear team, …",
        }
    )
    materials = LlmMaterialsTailorer(FakeLlmClient([reply])).tailor(story_cv, _MATCH)
    assert materials.summary == "Cut settlement runtime by 70%."
    assert materials.highlight_refs == ("story:3:r-abc123",)  # component-addressed provenance


def test_tailorer_falls_back_on_persistent_llm_failure() -> None:
    client = FakeLlmClient(default_text="not json at all")
    materials = LlmMaterialsTailorer(client).tailor(_cv(), _MATCH)
    assert materials == HeuristicMaterialsTailorer().tailor(_cv(), _MATCH)
    assert client.call_count == 2  # one attempt + one corrective retry


def test_tailorer_recovers_via_json_retry() -> None:
    client = FakeLlmClient(["oops, prose", _tailor_reply(["c0"])])
    materials = LlmMaterialsTailorer(client).tailor(_cv(), _MATCH)
    assert materials.summary == "Strong payments fit."


def test_tailorer_empty_cv_uses_heuristic_without_calling_llm() -> None:
    client = FakeLlmClient()
    LlmMaterialsTailorer(client).tailor(MasterCv(), _MATCH)
    assert client.call_count == 0


# --- drafter -----------------------------------------------------------------------


def test_drafter_returns_model_subject_and_body() -> None:
    client = FakeLlmClient([json.dumps({"subject": "Re: payments role", "body": "Hi Priya, …"})])
    message = LlmOutreachDrafter(client).draft(
        _MATERIALS, _JOB, Contact(name="Priya Raman", source="fixture")
    )
    assert message.subject == "Re: payments role"
    assert message.body == "Hi Priya, …"
    assert client.calls[0].tier is ModelTier.DEEP


def test_drafter_prompt_flags_missing_contact() -> None:
    client = FakeLlmClient([json.dumps({"subject": "s", "body": "b"})])
    LlmOutreachDrafter(client).draft(_MATERIALS, _JOB, None)
    prompt = client.calls[0].last_user_text
    assert "none found" in prompt
    system = client.calls[0].system
    assert system is not None and "do NOT invent" in system


def test_drafter_falls_back_on_persistent_llm_failure() -> None:
    client = FakeLlmClient(default_text="{not json")
    message = LlmOutreachDrafter(client).draft(_MATERIALS, _JOB, None)
    assert message == HeuristicOutreachDrafter().draft(_MATERIALS, _JOB, None)


def test_outreach_invented_numbers_fail_closed_to_the_heuristic() -> None:
    """§6: the outreach body — the artifact that leaves the machine — now has a number gate.

    A figure grounded in neither the tailored materials nor the posting fails closed to the
    heuristic drafter, which quotes the real highlights verbatim.
    """
    reply = json.dumps(
        {"subject": "Re: payments role", "body": "Hi Priya, I delivered 250% growth."}
    )  # 250 is in no material and no posting
    message = LlmOutreachDrafter(FakeLlmClient([reply])).draft(
        _MATERIALS, _JOB, Contact(name="Priya Raman", source="fixture")
    )
    assert message == HeuristicOutreachDrafter().draft(
        _MATERIALS, _JOB, Contact(name="Priya Raman", source="fixture")
    )
    assert "250" not in message.body


def test_outreach_supported_numbers_pass() -> None:
    materials = TailoredMaterials(
        summary="Cut runtime by 70%.",
        highlights=("Cut settlement runtime by 70%",),
        cover_letter="(letter)",
    )
    reply = json.dumps({"subject": "s", "body": "I cut runtime by 70% on the last team."})
    message = LlmOutreachDrafter(FakeLlmClient([reply])).draft(materials, _JOB, None)
    assert message.body == "I cut runtime by 70% on the last team."
