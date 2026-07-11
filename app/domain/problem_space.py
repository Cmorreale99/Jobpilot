"""Problem spaces + PAR bundles: v3.1's selection substrate (pure domain).

V3's story layer selects one story per confirmed entity: ``select_story_content``
picks exactly one problem, then pools ALL of the entity's actions and results under
it. When an entity spans several distinct problem spaces (the Cooper.ai case: FedEx
data integrity vs. Pacifica ingestion automation vs. dataset delivery), they blend —
an action from one space can sit beside a result from another. v3.1 makes the problem
space first-class: :func:`detect_problem_spaces` deterministically clusters an
entity's claims into :class:`ProblemSpace`\\ s, each holding one :class:`PARBundle`
per distinct problem with *candidate* actions/results for the user to pick from
(selection + single-bullet generation is Increment 4; persistence is Increment 3 —
nothing here touches a repository).

Detection is selection, never authorship: every label, problem, and candidate text
is a claim's text verbatim; ids are content hashes (stable across re-detection,
mirroring ``component_id``); clustering is deterministic — problems sharing
pain-point tags plus token overlap merge into one space, echoing the story layer's
coupling heuristics. Claims without a bar-clearing problem join a space only through
co-cited evidence and only when exactly one space matches; anything else stays
honestly uncovered (:func:`uncovered_claim_ids`) rather than guessed into a bundle.

The LLM detector (v3.1 Increment 7, ``app/llm/space_detection.py``, flag
``PROBLEM_SPACE_LLM_DETECTION`` via ``create_problem_space_detector``) replaces ONE
step and only one: the **grouping** of the distinct bar-clearing problem statements
(:class:`ProblemSpaceDetector` — the semantic judgment lexical relatedness can't
make, e.g. six facets of one carbon-market narrative). Seeding, attachment,
candidates, ids, and statuses stay this module's deterministic code either way, a
detector's output is sanitized against the input problems (unknown texts dropped,
missing ones restored as singletons — grouping is selection, never authorship), and
the bundle validators (``domain/bundle_validation.py``) gate both paths identically.

Naming note: :class:`ResultCandidate` here is the bundle-selection shape (carries
``result_type`` + ``outcome_quote`` + provenance stamps); the unrelated
``project_story.ResultCandidate`` is the metric-conflict detection shape.

Pure logic: no imports from ``integrations/`` implementations or ``llm/``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.domain.claims import (
    Claim,
    ClaimField,
    ClaimStatus,
    ResultKind,
    ResultType,
    pain_point_tags,
)
from app.domain.matching import tokenize
from app.domain.project_story import (
    LEFTOVER_PROBLEM_SPACE_ID,
    ProjectStory,
    assess_problem_text,
    component_id,
    rank_claims,
)

# --- Shapes -----------------------------------------------------------------------------


class BundleStatus(StrEnum):
    """Selection lifecycle of one PAR bundle.

    Detection derives ``requires_user_selection`` / ``missing_result``; ``ready`` is
    stamped only by the selection flow (Increment 4) after the validators pass —
    never by detection itself.
    """

    REQUIRES_USER_SELECTION = "requires_user_selection"
    READY = "ready"
    MISSING_RESULT = "missing_result"


@dataclass(frozen=True)
class BundleProblem:
    """The problem defining one bundle — a claim's problem text, verbatim.

    ``evidence_problem_space_ids`` / ``evidence_experience_ids`` are the spaces and
    entities of every cited claim, stamped at construction so
    ``validate_evidence_boundary`` is self-contained: a component assembled from
    claims across spaces (or entities) is detectable from the component alone.
    """

    problem_space_id: str
    bundle_id: str
    experience_id: int
    text: str
    claim_ids: tuple[int, ...]
    project_id: str | None = None
    evidence_problem_space_ids: tuple[str, ...] = ()
    evidence_experience_ids: tuple[int, ...] = ()
    field_type: ClaimField = ClaimField.PROBLEM


@dataclass(frozen=True)
class ActionCandidate:
    """One selectable action — a claim's action text, verbatim, with provenance stamps."""

    candidate_id: str
    problem_space_id: str
    bundle_id: str
    experience_id: int
    text: str
    claim_ids: tuple[int, ...]
    tools: tuple[str, ...] = ()
    project_id: str | None = None
    evidence_problem_space_ids: tuple[str, ...] = ()
    evidence_experience_ids: tuple[int, ...] = ()
    field_type: ClaimField = ClaimField.ACTION


@dataclass(frozen=True)
class ResultCandidate:
    """One selectable result — verbatim, span-deduped, never invented.

    ``result_type`` is the v3.1 category (coverage/delivery/…) read from
    ``result_metric_json["result_type"]``; ``outcome_quote`` is the verbatim span
    backing it (the same grounding bar as everywhere else — only the classification
    widened in v3.1, never the bar).
    """

    candidate_id: str
    problem_space_id: str
    bundle_id: str
    experience_id: int
    text: str
    claim_ids: tuple[int, ...]
    result_kind: ResultKind = ResultKind.QUALITATIVE_EVIDENCED
    result_type: ResultType | None = None
    outcome_quote: str | None = None
    project_id: str | None = None
    evidence_problem_space_ids: tuple[str, ...] = ()
    evidence_experience_ids: tuple[int, ...] = ()
    field_type: ClaimField = ClaimField.RESULT


@dataclass(frozen=True)
class PARBundle:
    """One complete Problem→Action→Result bundle: a problem plus its candidates.

    Candidates are the whole *space's* distinct actions/results (the space is the
    alignment boundary; within it, any action may serve any of its problems).
    ``selected_action_id`` / ``selected_result_id`` are the user's picks — always
    ``None`` at detection; Increment 4's selection flow sets them (validator-gated)
    and flips ``status`` to ``ready``.
    """

    bundle_id: str
    problem_space_id: str
    experience_id: int
    problem: BundleProblem
    action_candidates: tuple[ActionCandidate, ...]
    result_candidates: tuple[ResultCandidate, ...]
    selected_action_id: str | None = None
    selected_result_id: str | None = None
    status: BundleStatus = BundleStatus.REQUIRES_USER_SELECTION
    project_id: str | None = None


@dataclass(frozen=True)
class ProblemSpace:
    """One distinct problem space inside an experience.

    ``label`` is the space's highest-leverage problem text verbatim (never composed);
    ``scope`` is the deterministic pain-point tag summary of its member problems
    (``None`` when the lexicons match nothing).
    """

    problem_space_id: str
    experience_id: int
    label: str
    scope: str | None
    bundles: tuple[PARBundle, ...]
    project_id: str | None = None


# --- Detection: the grouping step is pluggable, everything else is deterministic ---------


class ProblemSpaceDetectionError(RuntimeError):
    """One entity's problem-space grouping failed outright (e.g. the LLM call failed).

    Raised instead of silently degrading to the heuristic — the caller skips the
    entity loudly (logged + recorded in ``validation_runs``), mirroring extraction's
    ``ClaimExtractionError`` and synthesis's ``StorySynthesisError``.
    """


@runtime_checkable
class ProblemSpaceDetector(Protocol):
    """Partitions one entity's distinct bar-clearing problem statements into spaces.

    The ONLY pluggable step of detection: given the problem texts (leverage order,
    deduped), return groups of those texts — statements describing the same
    underlying challenge together, separate workstreams apart. The caller sanitizes
    the output (unknown texts dropped, missing ones restored as singletons, first
    membership wins), so a detector can group but never author, omit, or duplicate.
    """

    def group_problems(self, problems: Sequence[str]) -> list[list[str]]: ...


@runtime_checkable
class GroupingStore(Protocol):
    """Persistence for recorded problem-space partitions (v3.1 Increment 8).

    Keyed by ``(user_id, fingerprint)`` where the fingerprint hashes the problem
    *set* (:func:`grouping_fingerprint`) — the grouping is a pure function of the
    problems, so entity identity is irrelevant to the key. ``put`` is first-write-wins
    (a recorded partition is a decision, not a cache to churn); groups are stored as
    normalized problem keys.
    """

    def get(self, user_id: str, fingerprint: str) -> list[list[str]] | None: ...

    def put(self, user_id: str, fingerprint: str, groups: Sequence[Sequence[str]]) -> None: ...


def grouping_fingerprint(problems: Sequence[str], *, version: str = "") -> str:
    """Stable hash of a problem SET (order-insensitive, whitespace/case-normalized).

    ``version`` lets a detector namespace its recordings (e.g. the LLM prompt
    version) so a semantic change to the grouper never silently replays partitions
    recorded under the old semantics.
    """
    keys = sorted({_normalized(p) for p in problems if p.strip()})
    material = "\x1e".join((version, *keys))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class PersistedGroupingDetector:
    """Records a detector's partitions and replays them for unchanged problem sets.

    The drift fix (live-verified failure mode, 2026-07-11): synthesis, eval, and every
    re-run over the same problems read ONE recorded partition instead of re-asking a
    non-deterministic grouper — the contamination invariant judges the partition that
    built the stories, and repeat LLM spend for unchanged entities drops to zero.
    What is stored is the *sanitized* partition (grounded, complete, disjoint), keyed
    by the problem-set fingerprint; a changed problem set means a new fingerprint and
    a fresh consultation. First write wins — re-grouping the same problems requires
    deleting the recorded row (or bumping ``version``), never a silent overwrite.
    """

    def __init__(
        self,
        inner: ProblemSpaceDetector,
        store: GroupingStore,
        user_id: str,
        *,
        version: str = "",
    ) -> None:
        self._inner = inner
        self._store = store
        self._user_id = user_id
        self._version = version

    def group_problems(self, problems: Sequence[str]) -> list[list[str]]:
        by_key = {_normalized(p): p for p in problems if p.strip()}
        fingerprint = grouping_fingerprint(problems, version=self._version)
        stored = self._store.get(self._user_id, fingerprint)
        if stored is not None:
            replayed = [[by_key[key] for key in group if key in by_key] for group in stored]
            return [group for group in replayed if group]
        sanitized = _sanitized_groups(self._inner.group_problems(problems), by_key)
        self._store.put(self._user_id, fingerprint, sanitized)
        return [[by_key[key] for key in group] for group in sanitized]


# Two bar-clearing problems share a space when their pain-point tags overlap AND they
# share at least this many content tokens (or one normalized text contains the other).
_MIN_SHARED_TOKENS = 2


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _related_problems(a: str, b: str) -> bool:
    """Do two problems describe the same space? Containment, or shared pain-point
    tags plus ≥2 shared content tokens — deterministic, mirroring
    ``results_address_problem``'s thresholds."""
    normalized_a, normalized_b = _normalized(a), _normalized(b)
    if normalized_a in normalized_b or normalized_b in normalized_a:
        return True
    if not (pain_point_tags(a) & pain_point_tags(b)):
        return False
    return len(set(tokenize(a)) & set(tokenize(b))) >= _MIN_SHARED_TOKENS


class HeuristicProblemSpaceDetector:
    """The deterministic, offline default: transitive union of lexically related
    problems (:func:`_related_problems`), preserving first-seen (leverage) order."""

    def group_problems(self, problems: Sequence[str]) -> list[list[str]]:
        groups: list[list[str]] = [[problem] for problem in problems]
        merged = True
        while merged:
            merged = False
            for i, left in enumerate(groups):
                for j in range(i + 1, len(groups)):
                    if any(_related_problems(a, b) for a in left for b in groups[j]):
                        left.extend(groups.pop(j))
                        merged = True
                        break
                if merged:
                    break
        return groups


def _sanitized_groups(
    raw_groups: Sequence[Sequence[str]], problem_texts: Mapping[str, str]
) -> list[list[str]]:
    """A detector's output as normalized-key groups — grounded, complete, disjoint.

    Grouping is selection, never authorship: a returned text matching no input
    problem is dropped; a problem placed in several groups keeps its first
    membership; a problem the detector omitted is restored as its own singleton
    space (in leverage order) — detection may merge, it may never lose a problem.
    """
    seen: set[str] = set()
    groups: list[list[str]] = []
    for raw_group in raw_groups:
        keys: list[str] = []
        for text in raw_group:
            key = _normalized(text)
            if key in problem_texts and key not in seen:
                seen.add(key)
                keys.append(key)
        if keys:
            groups.append(keys)
    for key in problem_texts:
        if key not in seen:
            groups.append([key])
    return groups


def _outcome_quote(claim: Claim) -> str | None:
    """The verbatim outcome span cited on the claim's Result link, if any."""
    return next(
        (
            link.outcome_quote
            for link in claim.evidence
            if link.field is ClaimField.RESULT and (link.outcome_quote or "").strip()
        ),
        None,
    )


def _result_span(claim: Claim) -> str | None:
    """The normalized outcome span backing a claim's Result (quote preferred)."""
    text = (claim.result_text or "").strip()
    if claim.result_kind is ResultKind.MISSING or not text:
        return None
    return _normalized(_outcome_quote(claim) or text)


def _claim_result_type(claim: Claim) -> ResultType | None:
    """The stored v3.1 result category, tolerating legacy/foreign metric JSON."""
    raw = (claim.result_metric_json or {}).get("result_type")
    if not isinstance(raw, str):
        return None
    try:
        return ResultType(raw)
    except ValueError:
        return None


def _cited_experience_ids(
    claim_ids: Sequence[int], claims_by_id: Mapping[int, Claim]
) -> tuple[int, ...]:
    return tuple(sorted({claims_by_id[i].experience_id for i in claim_ids}))


def detect_problem_spaces(
    experience_id: int,
    claims: Sequence[Claim],
    *,
    project_id: str | None = None,
    detector: ProblemSpaceDetector | None = None,
) -> list[ProblemSpace]:
    """Cluster one entity's claims into problem spaces (grouping step pluggable).

    * One bundle per distinct (normalized) problem that clears the §3.1 bar
      (``assess_problem_text``); the ``detector`` decides which problems share a
      space (default :class:`HeuristicProblemSpaceDetector` — transitive
      :func:`_related_problems`), and its output is sanitized so grouping can merge
      but never author, omit, or duplicate a problem. A detector is only consulted
      when there are ≥2 distinct problems (0/1 group trivially — no LLM spend).
    * A space's candidates are its member claims' distinct actions and distinct
      outcome spans (``ResultKind.MISSING`` never yields a candidate — a missing
      result is a follow-up, not a slot to fill).
    * A claim without a bar-clearing problem joins a space only when its cited
      evidence co-occurs with exactly ONE space's problem-bearing claims;
      ambiguous or unmatched claims stay uncovered (:func:`uncovered_claim_ids`).
    * Rejected claims never feed a space (the human ruled that content out).
    * ``status`` is ``missing_result`` when the space has no result candidates,
      else ``requires_user_selection``.
    """
    pool = [
        c
        for c in claims
        if c.experience_id == experience_id and c.status is not ClaimStatus.REJECTED
    ]
    ranked = rank_claims(pool)

    # Seed one bundle group per distinct bar-clearing problem, in leverage order.
    problem_texts: dict[str, str] = {}
    problem_claims: dict[str, list[Claim]] = {}
    unproblemed: list[Claim] = []
    for claim in ranked:
        candidate = (claim.problem_text or "").strip()
        if not candidate or assess_problem_text(candidate):
            unproblemed.append(claim)
            continue
        key = _normalized(candidate)
        problem_texts.setdefault(key, candidate)
        problem_claims.setdefault(key, []).append(claim)

    # The pluggable grouping step, over the distinct problem texts in leverage order.
    ordered_texts = list(problem_texts.values())
    if len(ordered_texts) >= 2:
        detector = detector or HeuristicProblemSpaceDetector()
        groups = _sanitized_groups(detector.group_problems(ordered_texts), problem_texts)
    else:
        groups = [[key] for key in problem_texts]

    # Attach problem-less claims by co-cited evidence — exactly one matching space,
    # judged against the seed claims only (order-independent, never a guess).
    seed_evidence = [
        {link.evidence_id for key in group for c in problem_claims[key] for link in c.evidence}
        for group in groups
    ]
    attached: dict[int, list[Claim]] = {index: [] for index in range(len(groups))}
    for claim in unproblemed:
        cited = {link.evidence_id for link in claim.evidence}
        hits = [index for index, ids in enumerate(seed_evidence) if ids & cited]
        if len(hits) == 1:
            attached[hits[0]].append(claim)

    spaces: list[ProblemSpace] = []
    for index, group in enumerate(groups):
        member_ids = {c.id for key in group for c in problem_claims[key]}
        member_ids |= {c.id for c in attached[index]}
        members = [c for c in ranked if c.id in member_ids]
        spaces.append(
            _build_space(
                group,
                members,
                problem_texts,
                experience_id=experience_id,
                project_id=project_id,
            )
        )
    return spaces


def _build_space(
    group: Sequence[str],
    members: Sequence[Claim],
    problem_texts: Mapping[str, str],
    *,
    experience_id: int,
    project_id: str | None,
) -> ProblemSpace:
    space_id = component_id("ps", " | ".join(sorted(group)))
    claims_by_id = {c.id: c for c in members}

    actions: list[ActionCandidate] = []
    seen_actions: set[str] = set()
    for claim in members:
        summary = claim.action_text.strip()
        if not summary or _normalized(summary) in seen_actions:
            continue
        seen_actions.add(_normalized(summary))
        cited = tuple(c.id for c in members if _normalized(c.action_text) == _normalized(summary))
        actions.append(
            ActionCandidate(
                candidate_id=component_id("a", summary),
                problem_space_id=space_id,
                bundle_id="",  # stamped per bundle below
                experience_id=experience_id,
                text=summary,
                claim_ids=cited,
                tools=claim.action_tools,
                project_id=project_id,
                evidence_problem_space_ids=(space_id,),
                evidence_experience_ids=_cited_experience_ids(cited, claims_by_id),
            )
        )

    results: list[ResultCandidate] = []
    seen_spans: set[str] = set()
    for claim in members:
        span = _result_span(claim)
        if span is None or span in seen_spans:
            continue  # one outcome span backs at most one candidate
        seen_spans.add(span)
        text = (claim.result_text or "").strip()
        cited = tuple(c.id for c in members if _result_span(c) == span)
        results.append(
            ResultCandidate(
                candidate_id=component_id("r", text),
                problem_space_id=space_id,
                bundle_id="",  # stamped per bundle below
                experience_id=experience_id,
                text=text,
                claim_ids=cited,
                result_kind=claim.result_kind,
                result_type=_claim_result_type(claim),
                outcome_quote=_outcome_quote(claim),
                project_id=project_id,
                evidence_problem_space_ids=(space_id,),
                evidence_experience_ids=_cited_experience_ids(cited, claims_by_id),
            )
        )

    status = BundleStatus.MISSING_RESULT if not results else BundleStatus.REQUIRES_USER_SELECTION
    bundles: list[PARBundle] = []
    for key in group:
        text = problem_texts[key]
        bundle_id = component_id("b", text)
        cited = tuple(c.id for c in members if _normalized(c.problem_text or "") == key)
        problem = BundleProblem(
            problem_space_id=space_id,
            bundle_id=bundle_id,
            experience_id=experience_id,
            text=text,
            claim_ids=cited,
            project_id=project_id,
            evidence_problem_space_ids=(space_id,),
            evidence_experience_ids=_cited_experience_ids(cited, claims_by_id),
        )
        bundles.append(
            PARBundle(
                bundle_id=bundle_id,
                problem_space_id=space_id,
                experience_id=experience_id,
                problem=problem,
                action_candidates=tuple(replace(a, bundle_id=bundle_id) for a in actions),
                result_candidates=tuple(replace(r, bundle_id=bundle_id) for r in results),
                status=status,
                project_id=project_id,
            )
        )

    tags: set[str] = set()
    for key in group:
        tags |= pain_point_tags(problem_texts[key])
    return ProblemSpace(
        problem_space_id=space_id,
        experience_id=experience_id,
        label=problem_texts[group[0]],
        scope=", ".join(sorted(tags)) or None,
        bundles=tuple(bundles),
        project_id=project_id,
    )


def bundle_from_story(
    story: ProjectStory,
    claims_by_id: Mapping[int, Claim],
    *,
    attested_problem: str | None = None,
    attested_result: str | None = None,
) -> PARBundle:
    """The selection bundle of one persisted story — built from its candidate lists.

    Selection (Increment 4) validates against what the human actually reviewed on the
    card, NOT against re-detection: a single-space entity's story pools claims wider
    than detection-time bundle candidates (Increment 3's pooling decision), and the
    story is the reviewed artifact either way. Candidate ids are the persisted
    component ids, so the card, the POST body, and the validators all speak the same
    addresses.

    Mirrors the render path's component resolution (``_prepare_renderable``): the
    Problem is the story's evidenced text or the ``story:{id}:problem`` attestation;
    Results are candidates whose cited claims still resolve with a non-missing kind,
    falling back to the ``story:{id}:result`` attestation when none do. Evidence
    stamps come from the cited claims, so ``validate_evidence_boundary`` catches a
    cross-entity citation from the bundle alone.
    """
    space_id = story.problem_space_id
    experience_id = story.experience_id
    content = story.content

    def cited_experience_ids(claim_ids: Sequence[int]) -> tuple[int, ...]:
        cited = {claims_by_id[i].experience_id for i in claim_ids if i in claims_by_id}
        return tuple(sorted(cited))

    problem_text = (content.problem_text or "").strip() or (attested_problem or "").strip()
    problem_refs = content.problem_refs if (content.problem_text or "").strip() else ()
    bundle_id = component_id("b", problem_text or space_id)
    problem = BundleProblem(
        problem_space_id=space_id,
        bundle_id=bundle_id,
        experience_id=experience_id,
        text=problem_text,
        claim_ids=problem_refs,
        evidence_problem_space_ids=(space_id,),
        evidence_experience_ids=cited_experience_ids(problem_refs),
    )

    actions = tuple(
        ActionCandidate(
            candidate_id=action.component_id,
            problem_space_id=space_id,
            bundle_id=bundle_id,
            experience_id=experience_id,
            text=action.summary,
            claim_ids=action.claim_ids,
            tools=action.tools,
            evidence_problem_space_ids=(space_id,),
            evidence_experience_ids=cited_experience_ids(action.claim_ids),
        )
        for action in content.actions
        if action.summary.strip() and any(i in claims_by_id for i in action.claim_ids)
    )

    results: list[ResultCandidate] = []
    for result in content.results:
        cited = [claims_by_id[i] for i in result.claim_ids if i in claims_by_id]
        live = [c for c in cited if c.result_kind is not ResultKind.MISSING]
        if not live:
            continue
        results.append(
            ResultCandidate(
                candidate_id=result.component_id,
                problem_space_id=space_id,
                bundle_id=bundle_id,
                experience_id=experience_id,
                text=result.text,
                claim_ids=result.claim_ids,
                result_kind=live[0].result_kind,
                result_type=_claim_result_type(live[0]),
                outcome_quote=result.outcome_quote,
                evidence_problem_space_ids=(space_id,),
                evidence_experience_ids=cited_experience_ids(result.claim_ids),
            )
        )
    if not results and (attested_result or "").strip():
        text = (attested_result or "").strip()
        results.append(
            ResultCandidate(
                candidate_id=component_id("r", text),
                problem_space_id=space_id,
                bundle_id=bundle_id,
                experience_id=experience_id,
                text=text,
                claim_ids=(),
                outcome_quote=text,
                evidence_problem_space_ids=(space_id,),
                evidence_experience_ids=(),
            )
        )

    status = BundleStatus.MISSING_RESULT if not results else BundleStatus.REQUIRES_USER_SELECTION
    if content.bundle_status == BundleStatus.READY.value and results:
        status = BundleStatus.READY
    return PARBundle(
        bundle_id=bundle_id,
        problem_space_id=space_id,
        experience_id=experience_id,
        problem=problem,
        action_candidates=actions,
        result_candidates=tuple(results),
        selected_action_id=content.selected_action_id,
        selected_result_id=content.selected_result_id,
        status=status,
    )


def synthesis_units(
    experience_id: int,
    claims: Sequence[Claim],
    *,
    detector: ProblemSpaceDetector | None = None,
) -> list[tuple[ProblemSpace | None, list[Claim]]]:
    """One synthesis unit per detected space, plus the leftover unit when needed.

    The Increment 3 partition ``run_story_synthesis`` builds stories from — promoted
    to the domain so the eval invariant (Increment 6) judges stories against the SAME
    claim-to-space partition synthesis used. With exactly ONE detected space, the
    entity's uncovered claims pool into it — v3's single-story semantics, safe because
    there is no second space to contaminate (and most real entities state one problem
    while their result claims cite different chunks, so strict co-citation would exile
    the results to a problem-less story). With several spaces, attachment stays
    strict: uncovered claims go to the entity's leftover unit (missing-problem
    question) rather than being guessed into a space — the Cooper.ai dataset-delivery
    case. A claim-less entity (or one with no detectable spaces) still yields exactly
    one leftover unit, so per-space synthesis never loses an entity v3 surfaced.
    """
    spaces = detect_problem_spaces(experience_id, claims, detector=detector)
    uncovered = set(uncovered_claim_ids(experience_id, claims, spaces))
    if len(spaces) == 1:
        member_ids = space_claim_ids(spaces[0]) | uncovered
        return [(spaces[0], [c for c in claims if c.id in member_ids])]
    units: list[tuple[ProblemSpace | None, list[Claim]]] = []
    for space in spaces:
        member_ids = space_claim_ids(space)
        units.append((space, [c for c in claims if c.id in member_ids]))
    if uncovered or not spaces:
        units.append((None, [c for c in claims if c.id in uncovered]))
    return units


def claim_space_ids(
    experience_id: int,
    claims: Sequence[Claim],
    *,
    detector: ProblemSpaceDetector | None = None,
) -> dict[int, str]:
    """Each live claim's synthesis-unit space id (the leftover unit included)."""
    mapping: dict[int, str] = {}
    for space, members in synthesis_units(experience_id, claims, detector=detector):
        space_id = space.problem_space_id if space is not None else LEFTOVER_PROBLEM_SPACE_ID
        for claim in members:
            mapping[claim.id] = space_id
    return mapping


def story_cross_space_claim_ids(
    story: ProjectStory,
    claims: Sequence[Claim],
    *,
    mapping: Mapping[int, str] | None = None,
) -> tuple[int, ...]:
    """Claim ids a persisted story cites across problem-space boundaries (Increment 6).

    A story's components must all live in ONE space — the core v3.1 invariant, judged
    here against the current claim-to-space partition (:func:`claim_space_ids`; pass
    ``mapping`` to reuse one partition across an entity's stories AND to keep the
    judgment on the same detector synthesis used — with ``PROBLEM_SPACE_LLM_DETECTION``
    on, judging LLM-partitioned stories against the heuristic partition would flag
    legitimate merges). Contamination means the cited claims resolve to two or more
    spaces; the offenders are the ids outside the story's home space (its own
    ``problem_space_id`` when cited, else the plurality space, deterministically
    tie-broken) — so a stale but internally-consistent story (all claims in one
    renamed space) stays clean, while a FedEx story citing a Pacifica claim is a hard
    scorecard regression even though the structural validator (same entity, valid
    refs) cannot see it.
    """
    if mapping is None:
        mapping = claim_space_ids(story.experience_id, claims)
    content = story.content
    cited: list[int] = list(content.problem_refs)
    for action in content.actions:
        cited.extend(action.claim_ids)
    for result in content.results:
        cited.extend(result.claim_ids)

    by_space: dict[str, set[int]] = {}
    for claim_id in cited:
        space_id = mapping.get(claim_id)
        if space_id is not None:  # cross-entity/orphan refs are the structural gate's job
            by_space.setdefault(space_id, set()).add(claim_id)
    if len(by_space) <= 1:
        return ()
    own = story.problem_space_id
    home = own if own in by_space else max(by_space, key=lambda s: (len(by_space[s]), s))
    return tuple(
        sorted(
            claim_id for space_id, ids in by_space.items() if space_id != home for claim_id in ids
        )
    )


def space_claim_ids(space: ProblemSpace) -> frozenset[int]:
    """Ids of every claim a space's bundles cite (its member claims).

    This is the synthesis unit for Increment 3's per-space stories: a story for this
    space is synthesized from exactly these claims, so its components can never cite
    across the space boundary by construction.
    """
    ids: set[int] = set()
    for bundle in space.bundles:
        ids.update(bundle.problem.claim_ids)
        for action in bundle.action_candidates:
            ids.update(action.claim_ids)
        for result in bundle.result_candidates:
            ids.update(result.claim_ids)
    return frozenset(ids)


def uncovered_claim_ids(
    experience_id: int, claims: Sequence[Claim], spaces: Sequence[ProblemSpace]
) -> tuple[int, ...]:
    """Ids of this entity's live claims that no detected space covers.

    Detection never guesses: a claim without a bar-clearing problem whose evidence
    co-occurs with zero or several spaces belongs to none of them. This is the
    honest-absence surface (mirroring the roster's unassigned-evidence queue) —
    Increment 3 keeps such claims out of per-space stories rather than blending them.
    """
    covered: set[int] = set()
    for space in spaces:
        for bundle in space.bundles:
            covered.update(bundle.problem.claim_ids)
            for action in bundle.action_candidates:
                covered.update(action.claim_ids)
            for result in bundle.result_candidates:
                covered.update(result.claim_ids)
    live = {
        c.id
        for c in claims
        if c.experience_id == experience_id and c.status is not ClaimStatus.REJECTED
    }
    return tuple(sorted(live - covered))


__all__ = [
    "ActionCandidate",
    "BundleProblem",
    "BundleStatus",
    "GroupingStore",
    "HeuristicProblemSpaceDetector",
    "PARBundle",
    "PersistedGroupingDetector",
    "ProblemSpace",
    "ProblemSpaceDetectionError",
    "ProblemSpaceDetector",
    "ResultCandidate",
    "bundle_from_story",
    "claim_space_ids",
    "detect_problem_spaces",
    "grouping_fingerprint",
    "space_claim_ids",
    "story_cross_space_claim_ids",
    "synthesis_units",
    "uncovered_claim_ids",
]
