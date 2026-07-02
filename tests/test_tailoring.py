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
