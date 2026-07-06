"""Guard tests for the live-shaped corpus fixture (docs/ARCHITECTURE_V3.md §5).

The fixture's whole value is matching the audited production shape; these tests pin
that shape so a refactor can't silently drift it back into a convenient toy corpus.
"""

from __future__ import annotations

import pytest
from app.domain.claims import ClaimStatus, ExperienceStatus
from app.domain.roster import detect_entity_overlaps

from tests.live_corpus import (
    CONFLICT_QUOTE_V1,
    CONFLICT_QUOTE_V2,
    ENTITY_SPECS,
    SHARED_OUTCOME_QUOTE,
    TOTAL_PENDING_CLAIMS,
    LiveCorpus,
    build_live_corpus,
)


@pytest.fixture
def corpus() -> LiveCorpus:
    return build_live_corpus()


def test_corpus_matches_the_audited_totals(corpus: LiveCorpus) -> None:
    repo, uid = corpus.repository, corpus.user_id
    confirmed = [e for e in repo.list_experiences(uid) if e.status is ExperienceStatus.CONFIRMED]
    assert len(confirmed) == 15
    assert TOTAL_PENDING_CLAIMS == 148
    assert len(repo.list_claims(uid, ClaimStatus.PENDING_REVIEW)) == 148
    assert len(repo.list_claims(uid, ClaimStatus.APPROVED)) == 1
    assert len(repo.list_claims(uid, ClaimStatus.REJECTED)) == 1


def test_per_entity_problem_result_distribution(corpus: LiveCorpus) -> None:
    pending = corpus.repository.list_claims(corpus.user_id, ClaimStatus.PENDING_REVIEW)
    for spec in ENTITY_SPECS:
        entity = corpus.entity(spec.name)
        claims = [c for c in pending if c.experience_id == entity.id]
        with_problem = sum(1 for c in claims if (c.problem_text or "").strip())
        with_result = sum(1 for c in claims if (c.result_text or "").strip())
        assert (len(claims), with_problem, with_result) == (
            spec.claims,
            spec.with_problem,
            spec.with_result,
        ), spec.name


def test_claim_less_entities_have_the_audited_evidence_shape(corpus: LiveCorpus) -> None:
    repo, uid = corpus.repository, corpus.user_id
    assert len(repo.list_assigned_evidence(uid, corpus.entity("Jobpilot").id)) == 116
    assert len(repo.list_assigned_evidence(uid, corpus.entity("DS4635").id)) == 10
    assert repo.list_assigned_evidence(uid, corpus.entity("Paper recommender system").id) == []


def test_cross_entity_duplicate_quote_is_present_and_detectable(corpus: LiveCorpus) -> None:
    """The live proof case (claims 472/513): the same outcome quote under two
    entities — exactly what the Phase 1 overlap pass exists to catch (T10)."""
    repo, uid = corpus.repository, corpus.user_id
    evidence = []
    for experience in repo.list_experiences(uid):
        evidence.extend(repo.list_assigned_evidence(uid, experience.id))
    overlaps = detect_entity_overlaps(repo.list_claims(uid), evidence)

    pair = {corpus.entity("OneWorld").id, corpus.entity("cameron-morreale-portfolio").id}
    matching = [
        o
        for o in overlaps
        if {o.experience_a_id, o.experience_b_id} == pair
        and SHARED_OUTCOME_QUOTE in o.shared_outcome_quotes
    ]
    assert matching, "the planted cross-entity duplicate must be detectable"


def test_conflicting_metric_wording_pair_is_present(corpus: LiveCorpus) -> None:
    massdep = corpus.entity("MassDEP")
    quotes = {
        link.outcome_quote
        for claim in corpus.claims_for("MassDEP")
        for link in claim.evidence
        if link.outcome_quote
    }
    assert {CONFLICT_QUOTE_V1, CONFLICT_QUOTE_V2} <= quotes
    assert massdep.status is ExperienceStatus.CONFIRMED
