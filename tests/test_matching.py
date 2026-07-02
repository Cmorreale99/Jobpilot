"""Pure two-stage matching: scoring, ranking, shortlist, determinism."""

from __future__ import annotations

from app.domain.cv import MasterCv, ParClaim
from app.domain.jobs import Job
from app.domain.matching import (
    HeuristicJobReranker,
    HeuristicJobScorer,
    build_profile,
    run_two_stage,
    shortlist,
)


def _cv() -> MasterCv:
    return MasterCv(
        claims=[
            ParClaim(
                action="Re-architected the settlement pipeline into idempotent sharded "
                "workers over a Kafka event log in Go and Python",
                source_ref="resume",
                problem="Payment settlement missed the banking cutoff",
                result="Cut settlement runtime and eliminated missed cutoffs",
            ),
            ParClaim(
                action="Introduced distributed tracing and observability across services",
                source_ref="resume",
            ),
        ],
    )


def _job(external_id: str, title: str, description: str) -> Job:
    return Job(
        source="mock",
        external_id=external_id,
        title=title,
        company="ACME",
        description=description,
    )


_PAYMENTS = _job(
    "j-pay",
    "Staff Backend Engineer, Payments",
    "Build idempotent sharded settlement workers over Kafka in Go and Python; "
    "distributed systems and observability.",
)
_MARKETING = _job(
    "j-mkt",
    "Marketing Manager",
    "Lead brand campaigns, SEO, social media, and email marketing storytelling.",
)
_SRE = _job(
    "j-sre",
    "Site Reliability Engineer",
    "Kubernetes, distributed tracing, observability, incident response.",
)


def test_relevant_job_scores_higher_than_irrelevant() -> None:
    scorer = HeuristicJobScorer()
    profile = build_profile(_cv())
    assert scorer.score(profile, _PAYMENTS).score > scorer.score(profile, _MARKETING).score


def test_matched_terms_are_reported() -> None:
    scored = HeuristicJobScorer().score(build_profile(_cv()), _PAYMENTS)
    assert "kafka" in scored.matched_terms
    assert "settlement" in scored.matched_terms


def test_two_stage_ranks_and_limits_top_n() -> None:
    jobs = [_MARKETING, _PAYMENTS, _SRE]
    matches = run_two_stage(
        _cv(), jobs, HeuristicJobScorer(), HeuristicJobReranker(), shortlist_size=10, top_n=2
    )
    assert len(matches) == 2
    assert [m.rank for m in matches] == [1, 2]
    assert matches[0].job.external_id == "j-pay"  # best fit ranks first
    assert matches[0].score >= matches[1].score
    assert "j-mkt" not in {m.job.external_id for m in matches}  # truncated out


def test_rationale_present_for_matches() -> None:
    matches = run_two_stage(
        _cv(), [_PAYMENTS], HeuristicJobScorer(), HeuristicJobReranker(), shortlist_size=10, top_n=1
    )
    assert "kafka" in matches[0].rationale.lower()


def test_shortlist_respects_size_and_order() -> None:
    profile = build_profile(_cv())
    scorer = HeuristicJobScorer()
    scored = [scorer.score(profile, j) for j in [_MARKETING, _PAYMENTS, _SRE]]
    top = shortlist(scored, 2)
    assert len(top) == 2
    assert top[0].job.external_id == "j-pay"


def test_matching_is_deterministic() -> None:
    jobs = [_SRE, _PAYMENTS, _MARKETING]
    args = (HeuristicJobScorer(), HeuristicJobReranker())
    first = run_two_stage(_cv(), jobs, *args, shortlist_size=10, top_n=3)
    second = run_two_stage(_cv(), jobs, *args, shortlist_size=10, top_n=3)
    assert [(m.job.external_id, m.rank, m.score) for m in first] == [
        (m.job.external_id, m.rank, m.score) for m in second
    ]


def test_empty_cv_yields_zero_scores() -> None:
    matches = run_two_stage(
        MasterCv(),
        [_PAYMENTS],
        HeuristicJobScorer(),
        HeuristicJobReranker(),
        shortlist_size=10,
        top_n=1,
    )
    assert matches[0].score == 0.0
    assert "no strong keyword overlap" in matches[0].rationale.lower()
