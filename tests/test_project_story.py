"""V3 Phase 2 exit tests — the story domain layer over the live-shaped corpus.

Covers the domain halves of the audit's T-tests (docs/V3_AUDIT.md §17): T1 (one story
per confirmed entity, pinned to 15), T5/T6 (missing Problem/Result never resume-ready,
gate refuses), T7 (the heuristic authors no prose; a technical repo reads
needs-problem), T8 (fatal structural findings), T9 (questions derived exactly from
missing fields, compound gaps included), T13 (component → claim → evidence walk),
T14 (numbers — digits AND number-words — verbatim in cited evidence), T16's
answer-validation half, plus ranking, coupling, conflicts, and the fingerprint.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.claims import Claim, ClaimStatus, ExperienceStatus, ResultKind, ResultStatus
from app.domain.par_validation import verbatim_in
from app.domain.project_story import (
    CLASSIFY_EVIDENCE_QUESTION,
    CLASSIFY_NO_EVIDENCE_QUESTION,
    MAX_STORY_ACTIONS,
    ComponentPresence,
    QuestionKind,
    ResultCandidate,
    StoryAction,
    StoryContent,
    StoryNotReadyError,
    StoryReadiness,
    StoryResult,
    approval_blockers,
    assert_approvable,
    assess_problem_text,
    component_id,
    compute_readiness,
    detect_metric_conflicts,
    leverage_score,
    problem_support_strength,
    rank_claims,
    resolve_component_evidence,
    results_address_problem,
    select_story_content,
    story_synthesis_fingerprint,
    unsupported_number_tokens,
    validate_story_structure,
)

from tests.live_corpus import (
    CONFLICT_QUOTE_V1,
    CONFLICT_QUOTE_V2,
    SHARED_OUTCOME_QUOTE,
    LiveCorpus,
    build_live_corpus,
)

RESUME_READY_ENTITIES = {
    "MassDEP",
    "Wellington Management",
    "OneWorld",
    "cameron-morreale-portfolio",
    "Embue",
    "Oasis",
    "BoardGameGeek",
}


@pytest.fixture(scope="module")
def corpus() -> LiveCorpus:
    # Module-scoped: these tests only read. Anything mutating builds its own.
    return build_live_corpus()


def _claims_by_id(corpus: LiveCorpus) -> dict[int, Claim]:
    return {c.id: c for c in corpus.repository.list_claims(corpus.user_id)}


def _draft(corpus: LiveCorpus, name: str) -> StoryContent:
    entity = corpus.entity(name)
    return select_story_content(entity.id, corpus.repository.list_claims(corpus.user_id))


def _readiness(corpus: LiveCorpus, name: str, **kwargs: object) -> StoryReadiness:
    entity = corpus.entity(name)
    evidence_count = len(corpus.repository.list_assigned_evidence(corpus.user_id, entity.id))
    return compute_readiness(
        _draft(corpus, name),
        entity.id,
        _claims_by_id(corpus),
        evidence_count=evidence_count,
        **kwargs,  # type: ignore[arg-type]
    )


# --- T1: claims collapse to one story per confirmed entity — 15, not ~12 ----------------


def test_t1_one_story_draft_per_confirmed_entity(corpus: LiveCorpus) -> None:
    confirmed = [
        e
        for e in corpus.repository.list_experiences(corpus.user_id)
        if e.status is ExperienceStatus.CONFIRMED
    ]
    assert len(confirmed) == 15
    claims = corpus.repository.list_claims(corpus.user_id)
    drafts = {e.id: select_story_content(e.id, claims) for e in confirmed}
    assert len(drafts) == 15
    # The three claim-less entities still get a (component-less) story draft.
    for name in ("Jobpilot", "DS4635", "Paper recommender system"):
        assert drafts[corpus.entity(name).id].is_empty()


def test_resume_ready_matches_the_audited_seven(corpus: LiveCorpus) -> None:
    ready = {
        spec_name for spec_name in corpus.entities if _readiness(corpus, spec_name).resume_ready
    }
    assert ready == RESUME_READY_ENTITIES


# --- T5/T6: no Problem / no Result ⇒ not resume_ready, approve gate refuses -------------


def test_t5_missing_problem_blocks_readiness_and_approval(corpus: LiveCorpus) -> None:
    readiness = _readiness(corpus, "investment-decision-workflow-engine")
    assert readiness.problem is ComponentPresence.MISSING
    assert readiness.result is ComponentPresence.EVIDENCED
    assert readiness.missing == ("problem",)
    assert not readiness.resume_ready
    with pytest.raises(StoryNotReadyError):
        assert_approvable(readiness)


def test_t6_missing_result_blocks_readiness_and_approval(corpus: LiveCorpus) -> None:
    readiness = _readiness(corpus, "Cooper.ai")
    assert readiness.problem is ComponentPresence.EVIDENCED
    assert readiness.result is ComponentPresence.MISSING
    assert readiness.missing == ("result",)
    assert not readiness.resume_ready
    with pytest.raises(StoryNotReadyError):
        assert_approvable(readiness)


def test_gate_passes_a_complete_story(corpus: LiveCorpus) -> None:
    readiness = _readiness(corpus, "MassDEP")
    assert readiness.resume_ready
    assert approval_blockers(readiness) == ()
    assert_approvable(readiness)  # must not raise


def test_attested_answers_flip_readiness_locally(corpus: LiveCorpus) -> None:
    """Answering recomputes readiness with no LLM call (§3.6); resume_ready is a
    precondition for approval, and typed answers are how gaps close."""
    readiness = _readiness(
        corpus,
        "personal-knowledge-os",
        attested_components={"problem", "result"},
        attested_problem_text=(
            "Notes and papers were scattered across five tools, so retrieving prior "
            "research took hours of manual searching"
        ),
    )
    assert readiness.problem is ComponentPresence.USER_ATTESTED
    assert readiness.result is ComponentPresence.USER_ATTESTED
    assert readiness.resume_ready
    assert_approvable(readiness)


# --- T7 (domain half): one-line code bugs never pass the problem bar; no authored prose --


def test_t7_bug_line_problems_read_missing(corpus: LiveCorpus) -> None:
    for name in ("personal-knowledge-os", "AI-Recovery-navigation"):
        draft = _draft(corpus, name)
        assert draft.problem_text is None, name  # the bar filtered the bug lines
        readiness = _readiness(corpus, name)
        assert readiness.problem is ComponentPresence.MISSING


def test_t7_selection_never_authors_prose(corpus: LiveCorpus) -> None:
    """Every component text is some claim's text verbatim — the heuristic selects."""
    claims = corpus.repository.list_claims(corpus.user_id)
    claim_texts = {(" ".join((c.problem_text or "").split())) for c in claims}
    claim_texts |= {" ".join(c.action_text.split()) for c in claims}
    claim_texts |= {" ".join((c.result_text or "").split()) for c in claims}
    for name in corpus.entities:
        draft = _draft(corpus, name)
        if draft.problem_text:
            assert " ".join(draft.problem_text.split()) in claim_texts
        for action in draft.actions:
            assert " ".join(action.summary.split()) in claim_texts
        for result in draft.results:
            assert " ".join(result.text.split()) in claim_texts


def test_rejected_claims_never_feed_a_story(corpus: LiveCorpus) -> None:
    rejected = corpus.repository.get_claim(corpus.rejected_claim_id)
    assert rejected is not None and rejected.status is ClaimStatus.REJECTED
    draft = _draft(corpus, "Wellington Management")
    cited: set[int] = set(draft.problem_refs)
    for action in draft.actions:
        cited |= set(action.claim_ids)
    for result in draft.results:
        cited |= set(result.claim_ids)
    assert rejected.id not in cited
    assert all(rejected.action_text != a.summary for a in draft.actions)


# --- T9: questions derived exactly from the missing fields (compound gaps included) ------


def test_t9_compound_gap_asks_both_questions(corpus: LiveCorpus) -> None:
    readiness = _readiness(corpus, "personal-knowledge-os")  # the live 28/2/0 case
    assert readiness.missing == ("problem", "result")
    kinds = [q.kind for q in readiness.questions]
    assert kinds == [QuestionKind.MISSING_PROBLEM, QuestionKind.MISSING_RESULT]


def test_t9_single_gaps_ask_exactly_one_question(corpus: LiveCorpus) -> None:
    problem_only = _readiness(corpus, "Stock Market Simulation")
    assert [q.kind for q in problem_only.questions] == [QuestionKind.MISSING_PROBLEM]

    result_only = _readiness(corpus, "Cooper.ai")
    assert [q.kind for q in result_only.questions] == [QuestionKind.MISSING_RESULT]


def test_t9_missing_action_is_asked_when_no_action_is_selected(corpus: LiveCorpus) -> None:
    """A story with a Problem and Result but zero selected Actions asks the
    missing-Action question (audit delta #5) and is not resume-ready. Built directly:
    ``select_story_content`` always derives Actions from claims, so this gap only
    appears on an edited/attested draft."""
    claims_by_id = _claims_by_id(corpus)
    massdep = corpus.entity("MassDEP")
    both = next(
        c
        for c in claims_by_id.values()
        if c.experience_id == massdep.id
        and (c.problem_text or "").strip()
        and not assess_problem_text(c.problem_text or "")
        and c.result_kind is not ResultKind.MISSING
    )
    content = StoryContent(
        problem_text=both.problem_text,
        problem_refs=(both.id,),
        actions=(),
        results=(
            StoryResult(
                component_id("r", both.result_text or ""), both.result_text or "", (both.id,)
            ),
        ),
    )
    readiness = compute_readiness(content, massdep.id, claims_by_id)
    assert readiness.problem is ComponentPresence.EVIDENCED
    assert readiness.actions == 0
    assert readiness.result is ComponentPresence.EVIDENCED
    assert readiness.missing == ("actions",)
    assert not readiness.resume_ready
    kinds = {q.kind for q in readiness.questions}
    assert QuestionKind.MISSING_ACTION in kinds
    assert QuestionKind.MISSING_PROBLEM not in kinds
    assert QuestionKind.MISSING_RESULT not in kinds
    with pytest.raises(StoryNotReadyError):
        assert_approvable(readiness)


def test_t9_claim_less_entities_get_one_classify_decision(corpus: LiveCorpus) -> None:
    with_evidence = _readiness(corpus, "Jobpilot")  # 116 chunks, zero claims
    assert [q.kind for q in with_evidence.questions] == [QuestionKind.INCLUDE_OR_INVENTORY]
    assert with_evidence.questions[0].text == CLASSIFY_EVIDENCE_QUESTION

    no_evidence = _readiness(corpus, "Paper recommender system")
    assert [q.kind for q in no_evidence.questions] == [QuestionKind.INCLUDE_OR_INVENTORY]
    assert no_evidence.questions[0].text == CLASSIFY_NO_EVIDENCE_QUESTION


def test_coupling_mismatch_becomes_a_question_not_a_blocker(corpus: LiveCorpus) -> None:
    """OneWorld's only Result is a hackathon placement; its Problem is manual toil —
    genuinely mismatched, so the card asks instead of flag-walling (§3.3)."""
    readiness = _readiness(corpus, "OneWorld")
    assert readiness.resume_ready  # a mismatch never blocks the gate by itself
    assert QuestionKind.COUPLING_MISMATCH in {q.kind for q in readiness.questions}

    massdep = _readiness(corpus, "MassDEP")  # results genuinely resolve the problem
    assert QuestionKind.COUPLING_MISMATCH not in {q.kind for q in massdep.questions}


# --- T8 (domain half): fatal structural findings on a draft ------------------------------


def _get_evidence(corpus: LiveCorpus):
    return corpus.repository.get_evidence


def test_t8_selected_drafts_are_structurally_clean(corpus: LiveCorpus) -> None:
    claims_by_id = _claims_by_id(corpus)
    for name in corpus.entities:
        entity = corpus.entity(name)
        violations = validate_story_structure(
            _draft(corpus, name), entity.id, claims_by_id, _get_evidence(corpus)
        )
        assert violations == [], (name, [str(v) for v in violations])


def test_t8_orphan_and_cross_entity_refs_are_fatal(corpus: LiveCorpus) -> None:
    claims_by_id = _claims_by_id(corpus)
    massdep = corpus.entity("MassDEP")
    wellington_claim = next(
        c
        for c in claims_by_id.values()
        if c.experience_id == corpus.entity("Wellington Management").id
        and c.status is ClaimStatus.PENDING_REVIEW
    )
    content = StoryContent(
        problem_text="A real enough problem statement costing the team time every week",
        problem_refs=(999_999,),
        actions=(
            StoryAction(
                component_id("a", "x"), "Built the thing with Python", (wellington_claim.id,)
            ),
            StoryAction(component_id("a", "y"), "Automated the other thing", ()),
        ),
    )
    codes = {
        v.code
        for v in validate_story_structure(content, massdep.id, claims_by_id, _get_evidence(corpus))
    }
    assert {"orphan_component_ref", "cross_entity_ref", "component_without_refs"} <= codes


def test_t8_duplicate_outcome_span_is_fatal(corpus: LiveCorpus) -> None:
    claims_by_id = _claims_by_id(corpus)
    entity = corpus.entity("MassDEP")
    result_claim = next(
        c
        for c in claims_by_id.values()
        if c.experience_id == entity.id and c.result_kind is not ResultKind.MISSING
    )
    quote = next(link.outcome_quote for link in result_claim.evidence if link.outcome_quote)
    duplicate = StoryContent(
        results=(
            StoryResult(
                component_id("r", "one"),
                result_claim.result_text or "",
                (result_claim.id,),
                quote,
            ),
            StoryResult(
                component_id("r", "two"),
                "Different words, same span",
                (result_claim.id,),
                quote,
            ),
        )
    )
    codes = [
        v.code
        for v in validate_story_structure(duplicate, entity.id, claims_by_id, _get_evidence(corpus))
    ]
    assert codes.count("duplicate_outcome_span") == 1


def test_t8_unsupported_number_in_composed_text_is_fatal(corpus: LiveCorpus) -> None:
    """A composed Problem stating a figure its cited evidence never states (the
    'used by ~160 professionals' attack, audit §14) fails structurally."""
    claims_by_id = _claims_by_id(corpus)
    entity = corpus.entity("MassDEP")
    cited = next(
        c
        for c in claims_by_id.values()
        if c.experience_id == entity.id and (c.problem_text or "").strip()
    )
    content = StoryContent(
        problem_text="Permit reviews were used by 160 professionals and cost 37% more each year",
        problem_refs=(cited.id,),
    )
    codes = {
        v.code
        for v in validate_story_structure(content, entity.id, claims_by_id, _get_evidence(corpus))
    }
    assert "unsupported_number" in codes


# --- T13 (domain half): component → claim ids → claim_evidence → chunks ------------------


def test_t13_every_component_walks_back_to_evidence(corpus: LiveCorpus) -> None:
    claims_by_id = _claims_by_id(corpus)
    for name in RESUME_READY_ENTITIES:
        draft = _draft(corpus, name)
        assert resolve_component_evidence(
            draft.problem_refs, claims_by_id, _get_evidence(corpus)
        ), name
        for action in draft.actions:
            assert resolve_component_evidence(
                action.claim_ids, claims_by_id, _get_evidence(corpus)
            ), (name, action.summary)
        for result in draft.results:
            resolved = resolve_component_evidence(
                result.claim_ids, claims_by_id, _get_evidence(corpus)
            )
            assert resolved, (name, result.text)
            if result.outcome_quote:
                assert any(
                    verbatim_in(result.outcome_quote, stored.chunk_text) for stored in resolved
                ), (name, result.outcome_quote)


# --- T14: numbers (digits AND number-words) verbatim in cited evidence -------------------


def test_t14_digit_tokens_must_be_supported() -> None:
    sources = ["Reduced permit export time from 10 hours to 2 hours"]
    assert unsupported_number_tokens("Cut export time from 10 hours to 2 hours", sources) == []
    assert unsupported_number_tokens("Improved throughput by 37%", sources) == ["37%"]
    # Comma-normalized: "1,417" and "1417" are the same number.
    assert unsupported_number_tokens("a 1,417-char commit", ["cites a 1417 char commit"]) == []


def test_t14_number_words_must_be_supported() -> None:
    sources = [f"Rebuilt the export checks, {CONFLICT_QUOTE_V1}."]
    assert unsupported_number_tokens("cutting export failures in half", sources) == []
    assert unsupported_number_tokens("doubled the export success rate", sources) == ["doubled"]


# --- Ranking (§3.2): deterministic leverage, no LLM self-scores ---------------------------


def test_leverage_tiers(corpus: LiveCorpus) -> None:
    massdep = corpus.claims_for("MassDEP")
    quantified_verified = next(
        c
        for c in massdep
        if c.result_kind is ResultKind.QUANTIFIED and c.result_status is ResultStatus.VERIFIED
    )
    qualitative = next(c for c in massdep if c.result_kind is ResultKind.QUALITATIVE_EVIDENCED)
    # MassDEP's problems all co-occur with Results; OneWorld (16/7/1) has both
    # problem-bearing-without-result and bare claims.
    oneworld = corpus.claims_for("OneWorld")
    problem_only = next(
        c
        for c in oneworld
        if (c.problem_text or "").strip() and c.result_kind is ResultKind.MISSING
    )
    bare = next(
        c
        for c in oneworld
        if not (c.problem_text or "").strip() and c.result_kind is ResultKind.MISSING
    )
    assert leverage_score(quantified_verified) == 4
    assert leverage_score(qualitative) == 3
    assert leverage_score(problem_only) == 2
    assert leverage_score(bare) == 1


def test_flagged_claims_lose_ties(corpus: LiveCorpus) -> None:
    """The flag tie-breaker sits directly after the leverage tier: within a tier,
    every integrity-flagged claim sorts after every unflagged one."""
    ranked = rank_claims(corpus.claims_for("MassDEP"))
    by_tier: dict[int, list[bool]] = {}
    for claim in ranked:
        by_tier.setdefault(leverage_score(claim), []).append(bool(claim.validation_flags))
    for tier, flagged_sequence in by_tier.items():
        first_flagged = flagged_sequence.index(True) if True in flagged_sequence else None
        if first_flagged is not None:
            assert all(flagged_sequence[first_flagged:]), tier


def test_attested_problem_reranks_selection(corpus: LiveCorpus) -> None:
    """§3.2: after a problem is attested, actions overlapping it outrank unrelated
    ones — the P→A arrow as architecture, not prompt vibes."""
    claims = corpus.claims_for("MassDEP")
    attested = "Permit reconciliation was slow because the scheduling step ran by hand"
    ranked = rank_claims(claims, attested_problem=attested)
    top_actions = " ".join(c.action_text.lower() for c in ranked[:5])
    assert "scheduling" in top_actions or "reconciliation" in top_actions
    # And relevance dominates: the first claim overlaps the attested problem.
    first_tokens = set(ranked[0].action_text.lower().split())
    assert {"scheduling", "reconciliation", "permit"} & first_tokens


def test_selection_caps_actions_and_dedupes_spans(corpus: LiveCorpus) -> None:
    draft = _draft(corpus, "personal-knowledge-os")  # 28 claims → never 28 actions
    assert 1 <= len(draft.actions) <= MAX_STORY_ACTIONS

    portfolio = _draft(corpus, "cameron-morreale-portfolio")
    spans = [" ".join((r.outcome_quote or r.text).split()).casefold() for r in portfolio.results]
    assert len(spans) == len(set(spans))  # one outcome span backs at most one component


# --- Problem bar + answer validation (T16's structural half) -----------------------------


def test_t16_answer_text_is_structurally_validated() -> None:
    assert assess_problem_text("Faster")  # one word can never flip resume_ready
    assert assess_problem_text("fixed a bug")
    assert assess_problem_text("See https://example.com/resume for details of it all")
    assert assess_problem_text("the .gitignore excluded the fixtures directory needed at runtime")
    assert assess_problem_text("pgvector lookup in retrieval.py returned stale rows")
    assert (
        assess_problem_text("Analysts spent six hours a week manually reconciling permit exports")
        == []
    )


def test_problem_support_strength_flags_weak_composition() -> None:
    chunks = ["The permit team spent 6 hours a week reconciling export spreadsheets by hand"]
    grounded = "The permit team spent hours reconciling export spreadsheets by hand"
    invented = "Democratizes recovery support for underserved communities nationwide"
    assert problem_support_strength(grounded, chunks) == "ok"
    assert problem_support_strength(invented, chunks) == "weak"


# --- Coupling + cross-source conflicts (§3.3) ---------------------------------------------


def test_results_address_problem_by_tag_or_token_overlap() -> None:
    problem = "The permit team spent 6 hours a week reconciling export spreadsheets by hand"
    assert results_address_problem(problem, ["Reduced permit export time from 10 hours to 2 hours"])
    assert not results_address_problem(problem, [SHARED_OUTCOME_QUOTE])
    assert results_address_problem("", [SHARED_OUTCOME_QUOTE])  # absence is not a mismatch


def test_metric_conflict_detected_with_recency_suggestion() -> None:
    older = ResultCandidate(
        claim_id=1,
        quote=CONFLICT_QUOTE_V1,
        source_ref="doc-massdep-resume-v1",
        source_date=datetime(2024, 1, 5, tzinfo=UTC),
    )
    newer = ResultCandidate(
        claim_id=2,
        quote=CONFLICT_QUOTE_V2,
        source_ref="doc-massdep-resume-v2",
        source_date=datetime(2026, 3, 1, tzinfo=UTC),
    )
    unrelated = ResultCandidate(
        claim_id=3, quote="Halved the onboarding survey dropout rate", source_ref="doc-x"
    )
    conflicts = detect_metric_conflicts([older, newer, unrelated])
    assert len(conflicts) == 1
    # Recency is the SUGGESTED tie-break (most recent first); the human decides.
    assert [c.claim_id for c in conflicts[0].candidates] == [2, 1]


def test_identical_or_unrelated_quotes_never_conflict() -> None:
    twin_a = ResultCandidate(1, CONFLICT_QUOTE_V1, "doc-a")
    twin_b = ResultCandidate(2, CONFLICT_QUOTE_V1, "doc-b")
    assert detect_metric_conflicts([twin_a, twin_b]) == []

    same_number_different_fact = [
        ResultCandidate(1, "Reduced load time by 40%", "doc-a"),
        ResultCandidate(2, "Grew signups 40% quarter over quarter", "doc-b"),
    ]
    assert detect_metric_conflicts(same_number_different_fact) == []


def test_conflict_question_carries_both_quotes(corpus: LiveCorpus) -> None:
    conflict = detect_metric_conflicts(
        [
            ResultCandidate(1, CONFLICT_QUOTE_V1, "doc-massdep-resume-v1"),
            ResultCandidate(2, CONFLICT_QUOTE_V2, "doc-massdep-resume-v2"),
        ]
    )
    readiness = _readiness(corpus, "MassDEP", conflicts=conflict)
    conflict_questions = [q for q in readiness.questions if q.kind is QuestionKind.METRIC_CONFLICT]
    assert len(conflict_questions) == 1
    assert len(conflict_questions[0].quotes) == 2
    assert any(CONFLICT_QUOTE_V1 in quote for quote in conflict_questions[0].quotes)
    assert any(CONFLICT_QUOTE_V2 in quote for quote in conflict_questions[0].quotes)


# --- Fingerprint (synthesis_hash economics) + component id stability ----------------------


def test_synthesis_fingerprint_is_stable_and_content_sensitive(corpus: LiveCorpus) -> None:
    claims = corpus.claims_for("MassDEP")
    evidence = corpus.repository.list_assigned_evidence(corpus.user_id, corpus.entity("MassDEP").id)
    first = story_synthesis_fingerprint(claims, evidence)
    reordered = story_synthesis_fingerprint(list(reversed(claims)), list(reversed(evidence)))
    assert first == reordered
    assert story_synthesis_fingerprint(claims[1:], evidence) != first
    assert story_synthesis_fingerprint(claims, evidence[1:]) != first


def test_component_ids_are_stable_content_addresses() -> None:
    assert component_id("a", "Built the  permit ingestion step") == component_id(
        "a", "built the permit ingestion step"
    )
    assert component_id("a", "Built the permit ingestion step") != component_id(
        "r", "Built the permit ingestion step"
    )
