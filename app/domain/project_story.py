"""Project stories: the V3 story domain layer (docs/ARCHITECTURE_V3.md, Phase 2).

V3's task is one coherent, verifiably true Project Story per confirmed roster entity:
first the Problem Space (why the project mattered), then the Actions taken against it,
then the Results proving the remedy worked. Claims (``domain/claims.py``) are demoted
to inventory; this module is everything the story layer can say **purely** — no I/O,
no LLM, no persistence:

* **Two axes, not one status** (§2.1): ``review_status`` is the only lifecycle enum
  (``draft → pending_review → approved | excluded``, approval a human act with a
  timestamp, exclusion requiring a retained reason); ``readiness`` is a derived
  struct recomputed from the story's components at read time — machine-assigned
  facts can never impersonate a human decision.
* **The resume-ready gate** (§3.4): a pure function over readiness — Problem present,
  ≥1 Action, Result present — enforced again at the API (409) and at render.
* **Targeted questions** (§3.6): derived from readiness, never persisted. Only typed
  answers persist — as ``user_attestation`` evidence addressed ``story:{id}:{component}``
  — and they are structurally validated with the same bar extracted problems face.
* **Deterministic leverage ranking** (§3.2): verified-quantified 4 > qualitative or
  attested 3 > problem-bearing 2 > bare action 1, deterministic tie-breakers, no LLM
  self-scores anywhere. An attested problem re-ranks selection locally.
* **Heuristic synthesis selects and gates — it never authors prose** (§3.8): every
  component's text is a claim's text verbatim; composed prose is the flag-gated LLM
  synthesizer's job (Phase 5), and its output still faces the structural checks here.
* **Structural checks** (§3.5): orphan/cross-entity refs, the number gate (digits AND
  number-words, resolved against cited evidence — never against generated text), and
  the one-outcome-span-one-component rule. Fatal findings quarantine a draft.
* **Provenance rule** (§2.2): story components cite ``claim_ids`` only. The resolver
  walk component → claims → ``claim_evidence`` → chunks is :func:`resolve_component_evidence`.

Everything here is unit-testable against the live-shaped corpus (``tests/live_corpus.py``).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.domain.applications import validate_transition
from app.domain.claims import (
    Claim,
    ClaimField,
    ClaimStatus,
    ResultKind,
    ResultStatus,
    StoredEvidence,
    pain_point_tags,
)
from app.domain.matching import tokenize
from app.domain.par_validation import looks_like_resume_artifact

# --- Review lifecycle (the ONLY status axis; §2.1) ------------------------------------


class StoryReviewStatus(StrEnum):
    """Human review state of one project story (``project_stories.review_status``)."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EXCLUDED = "excluded"


# approved/excluded are terminal for ordinary transitions: refreshing an approved story
# requires the explicit, logged invalidation path (repository ``invalidate_story``),
# never a status edit — re-synthesis must not override a human decision.
STORY_TRANSITIONS: dict[StoryReviewStatus, frozenset[StoryReviewStatus]] = {
    StoryReviewStatus.DRAFT: frozenset({StoryReviewStatus.PENDING_REVIEW}),
    StoryReviewStatus.PENDING_REVIEW: frozenset(
        {StoryReviewStatus.APPROVED, StoryReviewStatus.EXCLUDED}
    ),
    StoryReviewStatus.APPROVED: frozenset(),
    StoryReviewStatus.EXCLUDED: frozenset(),
}


class ExclusionReasonRequiredError(ValueError):
    """Raised when a story is excluded without a retained reason."""


class StoryNotReadyError(ValueError):
    """The resume-ready gate refused an approval (the API surfaces this as a 409)."""

    def __init__(self, blockers: Sequence[str]) -> None:
        super().__init__("story is not resume-ready: " + "; ".join(blockers))
        self.blockers = tuple(blockers)


class StoryReplaceError(ValueError):
    """Raised on an attempt to overwrite a story a human already decided on."""


def validate_story_transition(
    current: StoryReviewStatus, new: StoryReviewStatus, *, decision_note: str | None = None
) -> None:
    """Validate a review-status change; excluding requires a non-empty reason."""
    validate_transition(STORY_TRANSITIONS, current, new)
    if new is StoryReviewStatus.EXCLUDED and not (decision_note or "").strip():
        raise ExclusionReasonRequiredError(
            "excluding a story requires a reason (decision_note) — an inclusion "
            "decision is retained exactly like a claim rejection"
        )


# --- Story shapes ---------------------------------------------------------------------

# Component addressing for story-scoped attestations (`story:{id}:{component}`), and
# the `missing` entries of the readiness struct.
COMPONENT_PROBLEM = "problem"
COMPONENT_ACTIONS = "actions"
COMPONENT_RESULT = "result"

MAX_STORY_ACTIONS = 5  # 3–5 strategic actions, never 20 tiny ones (§3.2)

# The problem-space key of a story holding an entity's claims that no detected space
# covers (no bar-clearing problem, no unambiguous co-citation) — and of the sole story
# of an entity with no detected spaces at all. One leftover story per entity keeps
# problem-less claims and claim-less entities in the review flow (v3.1 Increment 3):
# per-space stories must never silently lose what v3's single story surfaced.
LEFTOVER_PROBLEM_SPACE_ID = "ps-leftover"


def component_id(prefix: str, text: str) -> str:
    """A stable component id derived from content, not from claim/story row ids.

    Persisting across re-synthesis of unapproved drafts (and freezing at approval)
    is what keeps downstream highlight references from dangling (§6).
    """
    normalized = " ".join(text.split()).casefold()
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


@dataclass(frozen=True)
class StoryAction:
    """One selected intervention against the Problem Space."""

    component_id: str
    summary: str
    claim_ids: tuple[int, ...]
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoryResult:
    """One selected outcome; ``outcome_quote`` is the verbatim span backing it."""

    component_id: str
    text: str
    claim_ids: tuple[int, ...]
    outcome_quote: str | None = None


@dataclass(frozen=True)
class StoryContent:
    """A story's components — what synthesis produces, what the row persists.

    Components cite **claim ids only** (§2.2): claims stay the relational provenance
    and approval substrate, so nothing at the story layer can cite provenance that
    bypassed PAR validation. ``synthesis_hash`` fingerprints the inputs (same skip
    economics as ``experiences.extraction_hash``).

    v3.1 (Increment 3): a story is one **problem space** of its entity, not the whole
    entity. ``problem_space_id`` is the space's stable content-hash id (or
    :data:`LEFTOVER_PROBLEM_SPACE_ID`); ``problem_space_label``/``problem_space_scope``
    mirror the detected space verbatim. ``actions``/``results`` are that space's
    *candidate* lists; ``selected_action_id``/``selected_result_id`` are the user's
    picks (Increment 4's selection flow — always ``None`` at synthesis, reset with the
    content when a machine draft is replaced). ``bundle_status`` is the selection
    lifecycle (``BundleStatus`` values, stored as its string).
    """

    problem_text: str | None = None
    problem_refs: tuple[int, ...] = ()
    actions: tuple[StoryAction, ...] = ()
    results: tuple[StoryResult, ...] = ()
    synthesis_hash: str | None = None
    problem_space_id: str | None = None
    problem_space_label: str | None = None
    problem_space_scope: str | None = None
    selected_action_id: str | None = None
    selected_result_id: str | None = None
    bundle_status: str | None = None

    def is_empty(self) -> bool:
        return not (self.problem_text or self.problem_refs or self.actions or self.results)


def derive_problem_space_id(content: StoryContent) -> str:
    """The problem-space key a story persists under.

    An explicit ``problem_space_id`` wins. Without one (legacy callers, the 0014
    backfill), a story with a problem derives the SAME id detection gives a
    single-problem space — ``component_id("ps", problem_text)`` — so re-synthesis
    upserts over the backfilled row instead of duplicating it; a problem-less story
    is the entity's leftover space.
    """
    if content.problem_space_id:
        return content.problem_space_id
    if (content.problem_text or "").strip():
        return component_id("ps", content.problem_text or "")
    return LEFTOVER_PROBLEM_SPACE_ID


def derive_bundle_status(content: StoryContent) -> str:
    """The stored selection status: explicit, else derived from result presence.

    Detection-time semantics (``BundleStatus`` in ``domain/problem_space.py``):
    a space with no result candidates is ``missing_result``; otherwise selection is
    still pending. ``ready`` is only ever stamped by the validator-gated selection
    flow, never derived here.
    """
    if content.bundle_status:
        return content.bundle_status
    return "requires_user_selection" if content.results else "missing_result"


@dataclass(frozen=True)
class ProjectStory:
    """One persisted project story — one per (confirmed entity, problem space)."""

    id: int
    user_id: str
    experience_id: int
    review_status: StoryReviewStatus
    content: StoryContent
    reviewed_at: datetime | None = None
    decision_note: str | None = None
    created_at: datetime | None = None

    @property
    def problem_space_id(self) -> str:
        return derive_problem_space_id(self.content)


# --- Answer / problem-space bar (§3.1, §3.6; T16) ---------------------------------------

_MIN_PROBLEM_SPACE_TOKENS = 6

# A "Problem" naming a source file is an implementation bug line, not a problem space
# (the live corpus's pgvector/`.gitignore` cases). Extends the resume-artifact shapes.
_CODE_ARTIFACT_RE = re.compile(
    r"(?:^|[\s/(\"'`])\.?[\w-]*\.(?:py|ipynb|js|jsx|ts|tsx|sql|sh|ya?ml|toml|cfg|ini|css|html)\b"
    r"|\.gitignore\b",
    re.IGNORECASE,
)


def assess_problem_text(text: str) -> list[str]:
    """Deterministic specificity reasons a text fails the problem-space bar (§3.1).

    Applied identically to machine-selected problem candidates AND user-typed answers
    (§3.6 — V2 validated machine text harder than human text; that was backwards).
    An empty list means the text clears the bar. This is a structural bar, not truth
    verification: entailment stays with the human, who sees the quotes.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ["empty"]
    reasons: list[str] = []
    if len(stripped.split()) < _MIN_PROBLEM_SPACE_TOKENS:
        reasons.append(
            f"too short to describe a problem space (< {_MIN_PROBLEM_SPACE_TOKENS} words)"
        )
    if looks_like_resume_artifact(stripped):
        reasons.append("looks like a resume artifact (contact line, link, or job header)")
    if _CODE_ARTIFACT_RE.search(stripped):
        reasons.append("names a source file — an implementation bug line, not a problem space")
    return reasons


# --- Readiness (derived struct, never persisted; §2.1) ----------------------------------


class ComponentPresence(StrEnum):
    """How a story's Problem or Result is supported."""

    EVIDENCED = "evidenced"
    USER_ATTESTED = "user_attested"
    MISSING = "missing"


class QuestionKind(StrEnum):
    """The §3.6 question taxonomy (the derivable kinds)."""

    MISSING_PROBLEM = "missing_problem"
    MISSING_ACTION = "missing_action"
    MISSING_RESULT = "missing_result"
    COUPLING_MISMATCH = "coupling_mismatch"
    METRIC_CONFLICT = "metric_conflict"
    INCLUDE_OR_INVENTORY = "include_or_inventory"


@dataclass(frozen=True)
class StoryQuestion:
    """One targeted question, derived at read time — never persisted, never stale."""

    kind: QuestionKind
    component: str  # problem | result | story
    text: str
    quotes: tuple[str, ...] = ()  # conflicting source quotes, when the kind carries them


MISSING_PROBLEM_QUESTION = (
    "What user, business, operational, or technical problem did this project solve?"
)
MISSING_ACTION_QUESTION = (
    "What did you personally build or change on this project? "
    "Name the concrete tools or systems involved."
)
MISSING_RESULT_QUESTION = (
    "What measurable or observable outcome came from this project? "
    "An approximate figure you can stand behind counts."
)
COUPLING_QUESTION = "Is this outcome from the same piece of work, or is this two stories?"
METRIC_CONFLICT_QUESTION = "Your sources disagree on this result — which is current?"
CLASSIFY_EVIDENCE_QUESTION = (
    "This project has evidence but no reviewable claims — re-extract it, or keep it "
    "as portfolio inventory / exclude it from the Master CV?"
)
CLASSIFY_NO_EVIDENCE_QUESTION = (
    "This project has no evidence — keep it as portfolio inventory or exclude it?"
)


@dataclass(frozen=True)
class StoryReadiness:
    """Machine readiness, recomputed from components at read time (§2.1).

    Never persisted as a scalar status; ``resume_ready`` is a precondition for
    approval, never a substitute for it. ``missing`` represents compound gaps
    (the live 28/2/0 case needs Problem AND Result — one enum value never could).
    """

    problem: ComponentPresence
    actions: int
    result: ComponentPresence
    resume_ready: bool
    missing: tuple[str, ...]
    questions: tuple[StoryQuestion, ...]


def _valid_refs(
    claim_ids: Sequence[int], experience_id: int, claims_by_id: Mapping[int, Claim]
) -> list[Claim]:
    """The cited claims that exist AND belong to this story's entity."""
    return [
        claims_by_id[i]
        for i in claim_ids
        if i in claims_by_id and claims_by_id[i].experience_id == experience_id
    ]


def compute_readiness(
    content: StoryContent,
    experience_id: int,
    claims_by_id: Mapping[int, Claim],
    *,
    attested_components: Collection[str] = (),
    attested_problem_text: str | None = None,
    evidence_count: int = 0,
    conflicts: Sequence[MetricConflict] = (),
) -> StoryReadiness:
    """Derive the readiness struct and its questions from a story's components.

    ``attested_components`` names the story-scoped attestations that exist
    (``story:{id}:problem`` / ``story:{id}:result``) — answering recomputes readiness
    locally, no LLM call (§3.6). ``evidence_count`` is the entity's assigned-chunk
    count, deciding which classify prompt a component-less story gets (§1).
    """
    if COMPONENT_PROBLEM in attested_components:
        problem = ComponentPresence.USER_ATTESTED
    elif (
        (content.problem_text or "").strip()
        and not assess_problem_text(content.problem_text or "")
        and _valid_refs(content.problem_refs, experience_id, claims_by_id)
    ):
        problem = ComponentPresence.EVIDENCED
    else:
        problem = ComponentPresence.MISSING

    actions = sum(
        1
        for action in content.actions
        if action.summary.strip() and _valid_refs(action.claim_ids, experience_id, claims_by_id)
    )

    evidenced_result = False
    attested_result = COMPONENT_RESULT in attested_components
    for result in content.results:
        for claim in _valid_refs(result.claim_ids, experience_id, claims_by_id):
            if claim.result_kind is ResultKind.MISSING:
                continue
            if claim.result_status is ResultStatus.USER_ATTESTED:
                attested_result = True
            else:
                evidenced_result = True
    if evidenced_result:
        result_presence = ComponentPresence.EVIDENCED
    elif attested_result:
        result_presence = ComponentPresence.USER_ATTESTED
    else:
        result_presence = ComponentPresence.MISSING

    missing: list[str] = []
    if problem is ComponentPresence.MISSING:
        missing.append(COMPONENT_PROBLEM)
    if actions == 0:
        missing.append(COMPONENT_ACTIONS)
    if result_presence is ComponentPresence.MISSING:
        missing.append(COMPONENT_RESULT)

    resume_ready = (
        problem is not ComponentPresence.MISSING
        and actions >= 1
        and result_presence is not ComponentPresence.MISSING
    )

    questions = _derive_questions(
        content,
        problem=problem,
        actions=actions,
        result=result_presence,
        attested_problem_text=attested_problem_text,
        evidence_count=evidence_count,
        conflicts=conflicts,
    )
    return StoryReadiness(
        problem=problem,
        actions=actions,
        result=result_presence,
        resume_ready=resume_ready,
        missing=tuple(missing),
        questions=questions,
    )


def _derive_questions(
    content: StoryContent,
    *,
    problem: ComponentPresence,
    actions: int,
    result: ComponentPresence,
    attested_problem_text: str | None,
    evidence_count: int,
    conflicts: Sequence[MetricConflict],
) -> tuple[StoryQuestion, ...]:
    if content.is_empty():
        # A claim-less entity gets ONE classify decision, not a wall of questions:
        # re-extract/classify when evidence exists, keep-or-exclude when none does.
        text = CLASSIFY_EVIDENCE_QUESTION if evidence_count else CLASSIFY_NO_EVIDENCE_QUESTION
        return (StoryQuestion(QuestionKind.INCLUDE_OR_INVENTORY, "story", text),)

    questions: list[StoryQuestion] = []
    if problem is ComponentPresence.MISSING:
        questions.append(
            StoryQuestion(QuestionKind.MISSING_PROBLEM, COMPONENT_PROBLEM, MISSING_PROBLEM_QUESTION)
        )
    if actions == 0:
        # No selected Action — a real gap the readiness `missing` tuple already marks,
        # but which V3 Phase 2 never turned into a targeted prompt (audit delta #5).
        # An Action is claim-derived, so this asks the user to supply the work; unlike
        # Problem/Result it has no story-scoped attestation slot.
        questions.append(
            StoryQuestion(QuestionKind.MISSING_ACTION, COMPONENT_ACTIONS, MISSING_ACTION_QUESTION)
        )
    if result is ComponentPresence.MISSING:
        questions.append(
            StoryQuestion(QuestionKind.MISSING_RESULT, COMPONENT_RESULT, MISSING_RESULT_QUESTION)
        )

    problem_text = (
        attested_problem_text
        if problem is ComponentPresence.USER_ATTESTED
        else content.problem_text
    )
    result_texts = [r.text for r in content.results if r.text.strip()]
    if (
        problem is not ComponentPresence.MISSING
        and result is not ComponentPresence.MISSING
        and problem_text
        and result_texts
        and not results_address_problem(problem_text, result_texts)
    ):
        questions.append(
            StoryQuestion(QuestionKind.COUPLING_MISMATCH, COMPONENT_RESULT, COUPLING_QUESTION)
        )

    for conflict in conflicts:
        questions.append(
            StoryQuestion(
                QuestionKind.METRIC_CONFLICT,
                COMPONENT_RESULT,
                METRIC_CONFLICT_QUESTION,
                quotes=tuple(candidate.describe() for candidate in conflict.candidates),
            )
        )
    return tuple(questions)


# --- The resume-ready gate (§3.4 — enforced here, at the API, and at render) ------------


def approval_blockers(readiness: StoryReadiness) -> tuple[str, ...]:
    """Why a story may NOT be approved (empty tuple = the gate passes)."""
    blockers: list[str] = []
    if readiness.problem is ComponentPresence.MISSING:
        blockers.append("the story has no Problem (evidenced or attested)")
    if readiness.actions < 1:
        blockers.append("the story has no selected Action")
    if readiness.result is ComponentPresence.MISSING:
        blockers.append("the story has no Result (evidenced or attested)")
    return tuple(blockers)


def assert_approvable(readiness: StoryReadiness) -> None:
    """The pure gate: raise :class:`StoryNotReadyError` unless readiness holds."""
    blockers = approval_blockers(readiness)
    if blockers:
        raise StoryNotReadyError(blockers)


# --- Deterministic leverage ranking (§3.2; no LLM self-scores anywhere) ------------------


def leverage_score(claim: Claim) -> int:
    """4 verified-quantified Result > 3 qualitative/attested Result > 2 problem-bearing
    action > 1 bare action. Code, not model output."""
    has_result = claim.result_kind is not ResultKind.MISSING
    if claim.result_kind is ResultKind.QUANTIFIED and claim.result_status is ResultStatus.VERIFIED:
        return 4
    if has_result or claim.result_status is ResultStatus.USER_ATTESTED:
        return 3
    if (claim.problem_text or "").strip():
        return 2
    return 1


def rank_claims(
    claims: Sequence[Claim],
    *,
    attested_problem: str | None = None,
    recency: Mapping[int, datetime] | None = None,
) -> list[Claim]:
    """Leverage-ranked claims; ties broken by no-integrity-flags, more evidence links,
    recency (when the caller resolved source timestamps), shorter text, then id.

    When the user has attested a problem, selection re-ranks against it (§3.2):
    claims whose Action overlaps the attested problem's tokens outrank unrelated
    ones — deterministic, local, no re-synthesis call.
    """
    problem_tokens = frozenset(tokenize(attested_problem)) if attested_problem else frozenset()

    def sort_key(claim: Claim) -> tuple[float, ...]:
        # Overlap COUNT, not presence: an entity's topic word appears in every one of
        # its actions, so a binary signal would be vacuously true across the board.
        relevance = len(problem_tokens & frozenset(tokenize(claim.action_text)))
        stamp = (recency or {}).get(claim.id)
        return (
            -relevance,
            -leverage_score(claim),
            1 if claim.validation_flags else 0,
            -len(claim.evidence),
            -(stamp.timestamp() if stamp is not None else float("-inf")),
            len(" ".join(claim.action_text.split())),
            claim.id,
        )

    return sorted(claims, key=sort_key)


# --- Heuristic synthesis: select and gate, NEVER author prose (§3.8) ---------------------


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def select_story_content(
    experience_id: int,
    claims: Sequence[Claim],
    *,
    attested_problem: str | None = None,
    recency: Mapping[int, datetime] | None = None,
    max_actions: int = MAX_STORY_ACTIONS,
) -> StoryContent:
    """The deterministic story draft: leverage-ranked claim SELECTION per component.

    Every component's text is a claim's text **verbatim** — deterministic template
    authorship is slop with a straight face (§3.8), so nothing here composes prose.
    Rejected claims never feed a story (the human ruled that content out); the
    Problem must clear the §3.1 bar (the live one-line-bug "problems" read missing);
    one outcome span backs at most one Result component (§3.7's within-story rule).
    ``synthesis_hash`` is left for the caller, which sees the evidence inputs.
    """
    pool = [
        c
        for c in claims
        if c.experience_id == experience_id and c.status is not ClaimStatus.REJECTED
    ]
    ranked = rank_claims(pool, attested_problem=attested_problem, recency=recency)

    problem_text: str | None = None
    problem_refs: tuple[int, ...] = ()
    for claim in ranked:
        candidate = (claim.problem_text or "").strip()
        if not candidate:
            continue
        if claim.problem_cost_dimension is None and claim.problem_inefficiency is None:
            continue  # a pain point declares what it costs; a task description is not one
        if assess_problem_text(candidate):
            continue
        problem_text = candidate
        problem_refs = tuple(
            c.id for c in ranked if _normalized(c.problem_text or "") == _normalized(candidate)
        )
        break

    actions: list[StoryAction] = []
    seen_actions: set[str] = set()
    for claim in ranked:
        summary = claim.action_text.strip()
        if not summary or _normalized(summary) in seen_actions:
            continue
        seen_actions.add(_normalized(summary))
        actions.append(
            StoryAction(
                component_id=component_id("a", summary),
                summary=summary,
                claim_ids=(claim.id,),
                tools=claim.action_tools,
            )
        )
        if len(actions) >= max_actions:
            break

    results: list[StoryResult] = []
    seen_spans: set[str] = set()
    for claim in ranked:
        text = (claim.result_text or "").strip()
        if claim.result_kind is ResultKind.MISSING or not text:
            continue
        quote = next(
            (
                link.outcome_quote
                for link in claim.evidence
                if link.field is ClaimField.RESULT and (link.outcome_quote or "").strip()
            ),
            None,
        )
        span_key = _normalized(quote or text)
        if span_key in seen_spans:
            continue  # one outcome span backs at most one component
        seen_spans.add(span_key)
        results.append(
            StoryResult(
                component_id=component_id("r", text),
                text=text,
                claim_ids=(claim.id,),
                outcome_quote=quote,
            )
        )

    return StoryContent(
        problem_text=problem_text,
        problem_refs=problem_refs,
        actions=tuple(actions),
        results=tuple(results),
    )


# --- Structural checks over a draft (§3.5; fatal findings quarantine it) -----------------


@dataclass(frozen=True)
class StoryViolation:
    """One structural finding on a story draft, machine-readable code + message."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


# Every structural code is fatal: the draft is quarantined (stays ``draft``, recorded
# in validation_runs, shown in the run report) rather than silently persisted.
FATAL_STORY_CODES = frozenset(
    {
        "orphan_component_ref",
        "cross_entity_ref",
        "component_without_refs",
        "unsupported_number",
        "duplicate_outcome_span",
    }
)

# Captures an optional K/M/B magnitude suffix ("195K+", "$4.01M") so notation
# variants of one quantity can be recognized as equivalent (see _canonical_values).
_DIGIT_TOKEN_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:[KkMmBb](?![a-zA-Z]))?%?")
_SUFFIX_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
_NUMBER_WORDS = frozenset(
    {
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "dozen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "half",
        "halved",
        "twice",
        "double",
        "doubled",
        "triple",
        "tripled",
    }
)


def _number_tokens(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """(digit tokens, number-word tokens) in a text — the T14 number inventory."""
    digits = frozenset(match.replace(",", "") for match in _DIGIT_TOKEN_RE.findall(text))
    words = frozenset(re.findall(r"[a-z]+", text.lower())) & _NUMBER_WORDS
    return digits, words


def _canonical_values(token: str) -> frozenset[str]:
    """A digit token's canonical numeric value(s): "$4.01M" → {"4.01", "4010000"}.

    Notation equivalence, never new information: "195K" and "195,000" are one
    quantity written two ways (a rephrasing extractor or composer must not fail the
    gate for it), while "196,000" still matches nothing "195K" grounds.
    """
    bare = token.replace(",", "").lstrip("$").rstrip("%").lower()
    values = {bare}
    if bare and bare[-1] in _SUFFIX_MULTIPLIERS:
        number = bare[:-1]
        try:
            expanded = float(number) * _SUFFIX_MULTIPLIERS[bare[-1]]
        except ValueError:
            return frozenset(values)
        values.add(number)
        values.add(f"{expanded:.10g}")
    return frozenset(values)


def unsupported_number_tokens(text: str, allowed_sources: Sequence[str]) -> list[str]:
    """Numbers (digits AND number-words) in ``text`` found in no allowed source.

    The story-layer number gate (T14): quantified anything in a component's text must
    appear in the cited evidence chunks or attestations — generated text never grounds
    further generation. Digit tokens are compared comma-normalized, and magnitude
    notation is equivalent ("195,000" is grounded by "195K+", "$4,010,000" by
    "$4.01M" — same quantity, different notation); number-words as whole words,
    case-insensitive.
    """
    digits, words = _number_tokens(text)
    allowed_digits: set[str] = set()
    allowed_words: set[str] = set()
    allowed_canonical: set[str] = set()
    for source in allowed_sources:
        source_digits, source_words = _number_tokens(source)
        allowed_digits |= source_digits
        allowed_words |= source_words
        for token in source_digits:
            allowed_canonical |= _canonical_values(token)
    unsupported_digits = [
        token
        for token in sorted(digits - allowed_digits)
        if not (_canonical_values(token) & allowed_canonical)
    ]
    return unsupported_digits + sorted(words - allowed_words)


def resolve_component_evidence(
    claim_ids: Sequence[int],
    claims_by_id: Mapping[int, Claim],
    get_evidence: Callable[[int], StoredEvidence | None],
) -> list[StoredEvidence]:
    """The provenance walk: component → claim ids → claim_evidence links → chunks.

    This is what makes §2.2's rule real — every story component resolves to evidence
    rows (source chunks or user attestations) through actual tables, never through
    un-FK'd blobs. Order is deterministic; duplicates collapse.
    """
    resolved: dict[int, StoredEvidence] = {}
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        for link in claim.evidence:
            if link.evidence_id not in resolved:
                stored = get_evidence(link.evidence_id)
                if stored is not None:
                    resolved[link.evidence_id] = stored
    return list(resolved.values())


def validate_story_structure(
    content: StoryContent,
    experience_id: int,
    claims_by_id: Mapping[int, Claim],
    get_evidence: Callable[[int], StoredEvidence | None],
) -> list[StoryViolation]:
    """Deterministic structural checks over a story draft (S1; all findings fatal).

    * Every component cites ≥1 claim id; every cited claim exists and belongs to this
      entity (container-entity blending and dangling refs are unrepresentable in a
      persisted story).
    * Every number in a component's text — including a composed Problem's — appears
      in the evidence its claims cite (resolved via the provenance walk, never
      checked against other generated text).
    * One outcome span backs at most one Result component.
    """
    violations: list[StoryViolation] = []

    def check_refs(label: str, claim_ids: Sequence[int]) -> None:
        if not claim_ids:
            violations.append(
                StoryViolation("component_without_refs", f"{label} cites no claim ids")
            )
            return
        for claim_id in claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                violations.append(
                    StoryViolation(
                        "orphan_component_ref", f"{label} cites unknown claim {claim_id}"
                    )
                )
            elif claim.experience_id != experience_id:
                violations.append(
                    StoryViolation(
                        "cross_entity_ref",
                        f"{label} cites claim {claim_id} belonging to experience "
                        f"{claim.experience_id}, not {experience_id}",
                    )
                )

    def check_numbers(label: str, text: str, claim_ids: Sequence[int]) -> None:
        evidence_texts = [
            stored.chunk_text
            for stored in resolve_component_evidence(claim_ids, claims_by_id, get_evidence)
        ]
        for token in unsupported_number_tokens(text, evidence_texts):
            violations.append(
                StoryViolation(
                    "unsupported_number",
                    f"{label} states {token!r}, which appears in no cited evidence chunk "
                    "or attestation",
                )
            )

    if (content.problem_text or "").strip():
        check_refs("problem", content.problem_refs)
        check_numbers("problem", content.problem_text or "", content.problem_refs)
    for action in content.actions:
        label = f"action {action.component_id}"
        check_refs(label, action.claim_ids)
        check_numbers(label, action.summary, action.claim_ids)
    seen_spans: dict[str, str] = {}
    for result in content.results:
        label = f"result {result.component_id}"
        check_refs(label, result.claim_ids)
        check_numbers(label, result.text, result.claim_ids)
        span_key = _normalized(result.outcome_quote or result.text)
        if span_key in seen_spans:
            violations.append(
                StoryViolation(
                    "duplicate_outcome_span",
                    f"{label} reuses the outcome span already backing "
                    f"result {seen_spans[span_key]}",
                )
            )
        else:
            seen_spans[span_key] = result.component_id
    return violations


# --- Problem support + coupling + cross-source conflicts (§3.1, §3.3) --------------------


def problem_support_strength(problem_text: str, cited_chunk_texts: Sequence[str]) -> str:
    """``"ok"`` or ``"weak"``: lexical overlap between a (composed) Problem and its
    cited chunks — the cheap deterministic backstop under composition (§3.1). No LLM
    judging LLM; a weak flag makes the card say so next to the rendered quotes.
    """
    problem_tokens = set(tokenize(problem_text))
    if not problem_tokens:
        return "weak"
    chunk_tokens: set[str] = set()
    for text in cited_chunk_texts:
        chunk_tokens |= set(tokenize(text))
    overlap = len(problem_tokens & chunk_tokens) / len(problem_tokens)
    return "ok" if overlap >= 0.5 else "weak"


def results_address_problem(problem_text: str, result_texts: Sequence[str]) -> bool:
    """The story-level Problem↔Result coupling check (§3.3, V2's ``resolves`` restated).

    True when any selected Result shares a pain-point tag (cost dimension /
    inefficiency lexicon) or ≥2 content tokens with the Problem. A mismatch is not a
    flag-wall — the card asks the coupling question instead.
    """
    if not problem_text.strip() or not result_texts:
        return True  # nothing to couple; absence is readiness data, not a mismatch
    problem_tags = pain_point_tags(problem_text)
    problem_tokens = set(tokenize(problem_text))
    for text in result_texts:
        if pain_point_tags(text) & problem_tags:
            return True
        if len(set(tokenize(text)) & problem_tokens) >= 2:
            return True
    return False


@dataclass(frozen=True)
class ResultCandidate:
    """One outcome quote with its provenance, for cross-source conflict detection."""

    claim_id: int
    quote: str
    source_ref: str
    source_date: datetime | None = None

    def describe(self) -> str:
        stamp = self.source_date.date().isoformat() if self.source_date else "undated"
        return f'"{self.quote}" ({self.source_ref}, {stamp})'


@dataclass(frozen=True)
class MetricConflict:
    """Sources disagreeing on the same result — a question, never a silent pick.

    ``candidates`` are recency-ordered (most recent first, undated last): recency is
    the SUGGESTED tie-break, the human is the actual tie-break. "Keep the strongest
    version" is banned — it is a pick-the-biggest-number generator (red-team 8).
    """

    candidates: tuple[ResultCandidate, ...]


def _related_quotes(a: str, b: str) -> bool:
    normalized_a, normalized_b = _normalized(a), _normalized(b)
    if normalized_a in normalized_b or normalized_b in normalized_a:
        return True
    tokens_a, tokens_b = set(tokenize(a)), set(tokenize(b))
    if not tokens_a or not tokens_b:
        return False
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b) >= 0.5


def detect_metric_conflicts(candidates: Sequence[ResultCandidate]) -> list[MetricConflict]:
    """Deterministic cross-source metric-conflict detection (§3.3; red-team 8).

    Two candidates conflict when they carry the same number tokens but different
    normalized wording that is lexically related (containment or ≥0.5 Jaccard) — the
    fixture pair: "cutting export failures in half" vs. "… in half across all
    regions". Identical wording is a duplicate, not a conflict; unrelated results
    sharing a number are different facts.
    """
    by_signature: dict[frozenset[str], list[ResultCandidate]] = {}
    for candidate in candidates:
        digits, words = _number_tokens(candidate.quote)
        signature = digits | words
        if signature:
            by_signature.setdefault(frozenset(signature), []).append(candidate)

    conflicts: list[MetricConflict] = []
    for group in by_signature.values():
        cluster: list[ResultCandidate] = []
        for candidate in group:
            if any(
                _related_quotes(candidate.quote, other.quote)
                and _normalized(candidate.quote) != _normalized(other.quote)
                for other in group
            ):
                cluster.append(candidate)
        if len({_normalized(c.quote) for c in cluster}) >= 2:
            ordered = sorted(
                cluster,
                key=lambda c: (
                    -(c.source_date.timestamp() if c.source_date else float("-inf")),
                    c.claim_id,
                ),
            )
            conflicts.append(MetricConflict(candidates=tuple(ordered)))
    return conflicts


# --- Synthesis fingerprint (same skip economics as extraction_hash) ----------------------


def story_synthesis_fingerprint(
    claims: Sequence[Claim], evidence: Sequence[StoredEvidence], *, scope: str = ""
) -> str:
    """Stable hash of one synthesis unit's inputs (its claims + assigned evidence).

    Unchanged inputs skip re-synthesis (``project_stories.synthesis_hash``), exactly
    like ``experiences.extraction_hash`` bounds extraction cost. v3.1 keys the
    fingerprint per (experience, problem space): ``scope`` is the space id and
    ``claims`` the space's member claims, so one space's edit never forces its
    siblings to re-synthesize.
    """
    claim_material = sorted(
        "\x1f".join(
            (
                str(claim.id),
                claim.status.value,
                claim.problem_text or "",
                claim.action_text,
                claim.result_text or "",
                ",".join(claim.validation_flags),
            )
        )
        for claim in claims
    )
    evidence_material = sorted(
        f"{stored.source_type}\x1f{stored.source_ref}\x1f{stored.chunk_text}" for stored in evidence
    )
    material = "\x1e".join((scope, *claim_material, "--", *evidence_material))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --- Synthesis engine contract (§3.8) -----------------------------------------------------


class StorySynthesisError(RuntimeError):
    """One entity's synthesis failed outright (e.g. the LLM call failed).

    Raised instead of silently degrading to a different engine — the caller skips the
    entity loudly (logged + recorded in ``validation_runs``), mirroring extraction's
    :class:`~app.domain.claims.ClaimExtractionError`. There is deliberately no silent
    heuristic fallback mid-run.
    """


@runtime_checkable
class StorySynthesizer(Protocol):
    """Turns one confirmed entity's claim inventory into a draft :class:`StoryContent`.

    The heuristic default **selects and gates, never authors** (§3.8); the LLM drop-in
    (``app/llm/story_synthesis.py``, flag-gated) composes the Problem statement and groups
    Actions — its output still faces ``validate_story_structure`` and the readiness gate,
    so composition never bypasses grounding. ``get_evidence`` resolves cited evidence for
    the LLM's grounding walk; the heuristic ignores it.
    """

    def synthesize(
        self,
        experience_id: int,
        claims: Sequence[Claim],
        get_evidence: Callable[[int], StoredEvidence | None],
        *,
        experience_name: str = "",
    ) -> StoryContent: ...


class HeuristicStorySynthesizer:
    """The deterministic, offline default: verbatim claim selection via
    :func:`select_story_content`."""

    def synthesize(
        self,
        experience_id: int,
        claims: Sequence[Claim],
        get_evidence: Callable[[int], StoredEvidence | None],
        *,
        experience_name: str = "",
    ) -> StoryContent:
        return select_story_content(experience_id, claims)


# --- Persistence contract -----------------------------------------------------------------


@runtime_checkable
class ProjectStoryRepository(Protocol):
    """Persistence for project stories. Semantics shared by every implementation:

    * One story per (experience, problem space) — the key is
      ``(experience_id, derive_problem_space_id(content))`` (v3.1 Increment 3; v3's
      one-per-entity rows live on under their backfilled space id).
    * ``upsert_draft`` creates or replaces a machine draft *of the same space*: an
      existing ``draft``/``pending_review`` row is replaced (status reset to
      ``draft`` — a new machine draft needs a new review) but a row a human decided
      on (``approved`` or ``excluded``) is NEVER replaced — that raises
      :class:`StoryReplaceError`. Refreshing a decided story requires the explicit
      ``invalidate_story`` first. (A story carrying typed answers is likewise never
      replaced — the caller checks for ``story:{id}:*`` attestations before
      upserting; the evidence table lives on the claim repository.)
    * ``transition_story`` validates :data:`STORY_TRANSITIONS`; excluding requires a
      ``decision_note``; approving stamps ``reviewed_at`` (a human act with a
      timestamp — the readiness gate itself is enforced by the API layer, which has
      the claims to compute readiness from).
    * ``invalidate_story`` is the roster cascade AND the explicit un-approve: every
      one of the entity's stories returns to ``draft`` with its prior decision
      recorded in ``decision_note`` (merging or discarding an entity invalidates its
      stories; re-synthesis never silently overwrites what a human approved).
    * ``delete_draft`` removes a machine draft outright — the stale-space cleanup
      when re-detection dissolves a space. A decided story raises
      :class:`StoryReplaceError`; deletion is never how a human decision ends.
    * ``record_selection`` persists the user's validated bundle picks
      (``selected_action_id``/``selected_result_id``) and stamps
      ``bundle_status="ready"`` — the ONLY writer of ``ready`` (detection never
      derives it). Validation happens in the service layer before this is called;
      a decided story raises :class:`StoryReplaceError`.
    """

    def upsert_draft(
        self, user_id: str, experience_id: int, content: StoryContent
    ) -> ProjectStory: ...

    def get_story(self, story_id: int) -> ProjectStory | None: ...

    def get_story_for_experience(self, user_id: str, experience_id: int) -> ProjectStory | None: ...

    def get_story_for_space(
        self, user_id: str, experience_id: int, problem_space_id: str
    ) -> ProjectStory | None: ...

    def list_stories_for_experience(
        self, user_id: str, experience_id: int
    ) -> list[ProjectStory]: ...

    def list_stories(
        self, user_id: str, status: StoryReviewStatus | None = None
    ) -> list[ProjectStory]: ...

    def transition_story(
        self,
        story_id: int,
        new_status: StoryReviewStatus,
        *,
        decision_note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> ProjectStory: ...

    def invalidate_story(self, experience_id: int, *, reason: str) -> list[ProjectStory]: ...

    def delete_draft(self, story_id: int) -> None: ...

    def record_selection(
        self, story_id: int, selected_action_id: str, selected_result_id: str
    ) -> ProjectStory: ...


__all__ = [
    "CLASSIFY_EVIDENCE_QUESTION",
    "CLASSIFY_NO_EVIDENCE_QUESTION",
    "COMPONENT_ACTIONS",
    "COMPONENT_PROBLEM",
    "COMPONENT_RESULT",
    "COUPLING_QUESTION",
    "FATAL_STORY_CODES",
    "LEFTOVER_PROBLEM_SPACE_ID",
    "MAX_STORY_ACTIONS",
    "METRIC_CONFLICT_QUESTION",
    "MISSING_ACTION_QUESTION",
    "MISSING_PROBLEM_QUESTION",
    "MISSING_RESULT_QUESTION",
    "STORY_TRANSITIONS",
    "ComponentPresence",
    "ExclusionReasonRequiredError",
    "HeuristicStorySynthesizer",
    "MetricConflict",
    "ProjectStory",
    "ProjectStoryRepository",
    "QuestionKind",
    "StorySynthesizer",
    "StorySynthesisError",
    "ResultCandidate",
    "StoryAction",
    "StoryContent",
    "StoryNotReadyError",
    "StoryQuestion",
    "StoryReadiness",
    "StoryReplaceError",
    "StoryResult",
    "StoryReviewStatus",
    "StoryViolation",
    "approval_blockers",
    "assert_approvable",
    "assess_problem_text",
    "component_id",
    "compute_readiness",
    "derive_bundle_status",
    "derive_problem_space_id",
    "detect_metric_conflicts",
    "leverage_score",
    "problem_support_strength",
    "rank_claims",
    "resolve_component_evidence",
    "results_address_problem",
    "select_story_content",
    "story_synthesis_fingerprint",
    "unsupported_number_tokens",
    "validate_story_transition",
]
