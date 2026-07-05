"""Heuristic tailoring: relevance selection, verbatim evidence, determinism."""

from __future__ import annotations

from app.domain.cv import MasterCv, ParClaim
from app.domain.jobs import Job, JobMatch
from app.domain.tailoring import (
    HeuristicMaterialsTailorer,
    TailoredMaterials,
    render_claim,
    select_claims,
)

_PAYMENTS_CLAIM = ParClaim(
    action="Re-architected the settlement pipeline into idempotent sharded workers "
    "over a Kafka event log in Go and Python",
    source_ref="resume",
    problem="Payment settlement missed the banking cutoff",
    result="Cut settlement runtime by 70%",
)
_TRACING_CLAIM = ParClaim(
    action="Introduced distributed tracing and observability across services",
    source_ref="resume",
)
_BAKING_CLAIM = ParClaim(
    action="Won a regional sourdough baking championship",
    source_ref="portfolio",
)


def _cv() -> MasterCv:
    return MasterCv(claims=[_BAKING_CLAIM, _PAYMENTS_CLAIM, _TRACING_CLAIM], version=3)


def _match(job: Job) -> JobMatch:
    return JobMatch(
        job=job, score=0.8, rank=1, rationale="fits", matched_terms=("kafka", "settlement")
    )


_PAYMENTS_JOB = Job(
    source="mock",
    external_id="j-pay",
    title="Staff Backend Engineer, Payments",
    company="Ledgerline",
    description="Idempotent settlement workers over Kafka in Go and Python; observability.",
)


def test_selects_relevant_claims_over_irrelevant() -> None:
    claims = select_claims(_cv(), _match(_PAYMENTS_JOB))
    assert _PAYMENTS_CLAIM in claims
    assert _BAKING_CLAIM not in claims  # zero overlap -> excluded


def test_zero_overlap_cv_falls_back_to_real_claims() -> None:
    cv = MasterCv(claims=[_BAKING_CLAIM])
    claims = select_claims(cv, _match(_PAYMENTS_JOB))
    assert claims == [_BAKING_CLAIM]  # arbitrary but real — never fabricated filler


def test_render_claim_is_verbatim() -> None:
    line = render_claim(_PAYMENTS_CLAIM)
    assert _PAYMENTS_CLAIM.action in line
    assert "Cut settlement runtime by 70%" in line


def test_materials_quote_claims_verbatim_and_name_the_job() -> None:
    materials = HeuristicMaterialsTailorer().tailor(_cv(), _match(_PAYMENTS_JOB))
    assert "Staff Backend Engineer, Payments" in materials.summary
    assert "Ledgerline" in materials.cover_letter
    assert any(_PAYMENTS_CLAIM.action in h for h in materials.highlights)
    for highlight in materials.highlights:
        assert highlight in materials.cover_letter


def test_tailoring_is_deterministic() -> None:
    tailorer = HeuristicMaterialsTailorer()
    assert tailorer.tailor(_cv(), _match(_PAYMENTS_JOB)) == tailorer.tailor(
        _cv(), _match(_PAYMENTS_JOB)
    )


def test_materials_json_round_trip() -> None:
    materials = HeuristicMaterialsTailorer().tailor(_cv(), _match(_PAYMENTS_JOB))
    assert TailoredMaterials.from_json(materials.to_json()) == materials


# --- Phase 3: claim-id traceability + the number-factuality gate ---------------------------


def test_claim_ref_id_parses_ledger_refs_only() -> None:
    from app.domain.tailoring import claim_ref_id

    ledger = ParClaim(action="x", source_type="approved_claim", source_ref="claim:41")
    assert claim_ref_id(ledger) == 41
    assert claim_ref_id(ParClaim(action="x", source_ref="resume")) is None
    assert claim_ref_id(ParClaim(action="x", source_ref="claim:not-a-number")) is None


def test_heuristic_highlights_carry_their_claim_ids() -> None:
    from app.domain.tailoring import claim_ref_id, select_claims

    cv = MasterCv(
        claims=[
            ParClaim(
                action=c.action,
                problem=c.problem,
                result=c.result,
                source_type="approved_claim",
                source_ref=f"claim:{i + 10}",
            )
            for i, c in enumerate([_PAYMENTS_CLAIM, _TRACING_CLAIM])
        ],
        version=3,
    )
    materials = HeuristicMaterialsTailorer().tailor(cv, _match(_PAYMENTS_JOB))
    assert len(materials.highlight_claim_ids) == len(materials.highlights)
    selected = select_claims(cv, _match(_PAYMENTS_JOB))
    assert materials.highlight_claim_ids == tuple(claim_ref_id(c) or 0 for c in selected)
    assert all(i >= 10 for i in materials.highlight_claim_ids)


def test_unsupported_numbers_finds_invented_metrics() -> None:
    from app.domain.tailoring import unsupported_numbers

    allowed = ["Cut settlement runtime by 70%", "took 10 hours per week"]
    assert unsupported_numbers("Cut runtime by 70% in 10 hours", allowed) == []
    assert unsupported_numbers("Drove $8M in savings, 70% faster", allowed) == ["8"]
    assert unsupported_numbers("Improved 1,000 pipelines", ["handled 1000 pipelines"]) == []


def test_materials_json_round_trip_including_legacy_rows() -> None:
    materials = TailoredMaterials(
        summary="s",
        highlights=("h1", "h2"),
        cover_letter="c",
        highlight_claim_ids=(41, 42),
    )
    assert TailoredMaterials.from_json(materials.to_json()) == materials
    # Legacy rows predate highlight_claim_ids: they deserialize with an empty tuple.
    legacy = {"summary": "s", "highlights": ["h1"], "cover_letter": "c"}
    assert TailoredMaterials.from_json(legacy).highlight_claim_ids == ()
