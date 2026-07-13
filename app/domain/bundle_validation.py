"""The four PAR-bundle validators: v3.1's pre-generation hard gates (pure).

These run before any bullet is composed (and again at selection time, Increment 4):
a bullet that mixes problem spaces, cites a candidate outside its bundle, blends
evidence across spaces or projects, or fills a missing result is *unrepresentable* —
the gate refuses, it never repairs. Violations mirror ``StoryViolation``
(machine-readable code + message); ``missing_result`` additionally carries
``next_action`` because a bundle with no result candidates is a targeted question
for the human (the 7-option result-type follow-up), never a slot for generation.

Pure logic: no imports from ``integrations/`` implementations or ``llm/``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Protocol

from app.domain.claims import ClaimField
from app.domain.problem_space import (
    ActionCandidate,
    BundleProblem,
    PARBundle,
    ResultCandidate,
)

# Violation codes (machine-readable; every one is fatal to bullet generation).
PROBLEM_SPACE_MISMATCH = "problem_space_mismatch"
SELECTED_ACTION_OUTSIDE_BUNDLE = "selected_action_outside_bundle"
SELECTED_RESULT_OUTSIDE_BUNDLE = "selected_result_outside_bundle"
CROSS_PROBLEM_SPACE_CONTAMINATION = "cross_problem_space_contamination"
CROSS_PROJECT_CONTAMINATION = "cross_project_contamination"
MISSING_RESULT = "missing_result"
UNSUPPORTED_PAIRING = "unsupported_pairing"

FATAL_BUNDLE_CODES = frozenset(
    {
        PROBLEM_SPACE_MISMATCH,
        SELECTED_ACTION_OUTSIDE_BUNDLE,
        SELECTED_RESULT_OUTSIDE_BUNDLE,
        CROSS_PROBLEM_SPACE_CONTAMINATION,
        CROSS_PROJECT_CONTAMINATION,
        MISSING_RESULT,
        UNSUPPORTED_PAIRING,
    }
)

# Action→result relationship statuses (MASTER CV REPAIR §9.1). Only the first three
# may generate a bullet implying causality (§9.2); ``unknown`` stays unknown — a
# shared problem-space id is deliberately NOT a relationship (§5.6.5/§9.3).
RELATIONSHIP_DIRECT = "direct"  # the same claim: explicit source linkage
RELATIONSHIP_SAME_SOURCE = "same_source_section"  # distinct claims citing shared evidence
RELATIONSHIP_USER_ATTESTED = "user_attested"  # the user supplied the result for this story
RELATIONSHIP_UNKNOWN = "unknown"

SUPPORTED_RELATIONSHIPS = frozenset(
    {RELATIONSHIP_DIRECT, RELATIONSHIP_SAME_SOURCE, RELATIONSHIP_USER_ATTESTED}
)

# The next_action a missing_result violation carries: ask the targeted result-type
# follow-up (Increment 4 reuses ``QuestionKind.MISSING_RESULT`` with the 7 options).
ASK_TARGETED_FOLLOWUP = "ask_targeted_followup"


@dataclass(frozen=True)
class BundleViolation:
    """One finding from a bundle validator, machine-readable code + message."""

    code: str
    message: str
    next_action: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class EvidenceBounded(Protocol):
    """Any bundle component carrying evidence provenance stamps.

    Satisfied by :class:`BundleProblem`, :class:`ActionCandidate`, and
    :class:`ResultCandidate` — the boundary validator reads only these stamps,
    so it works on any of them without knowing which.
    """

    @property
    def problem_space_id(self) -> str: ...

    @property
    def experience_id(self) -> int: ...

    @property
    def text(self) -> str: ...

    @property
    def field_type(self) -> ClaimField: ...

    @property
    def evidence_problem_space_ids(self) -> tuple[str, ...]: ...

    @property
    def evidence_experience_ids(self) -> tuple[int, ...]: ...


def validate_problem_space_alignment(
    problem: BundleProblem,
    action: ActionCandidate,
    result: ResultCandidate | None = None,
) -> list[BundleViolation]:
    """Every component of a bullet shares ONE ``problem_space_id`` — or refuse.

    This is the core v3.1 invariant: a FedEx problem can never sit beside a
    Pacifica action or result. ``result`` is optional only because alignment is
    also checked at action-selection time; a generated bullet always has all three.
    """
    components: list[tuple[str, str]] = [
        ("problem", problem.problem_space_id),
        ("action", action.problem_space_id),
    ]
    if result is not None:
        components.append(("result", result.problem_space_id))
    spaces = {space for _, space in components}
    if len(spaces) <= 1:
        return []
    detail = ", ".join(f"{name}={space}" for name, space in components)
    return [
        BundleViolation(
            PROBLEM_SPACE_MISMATCH,
            f"components span {len(spaces)} problem spaces ({detail}) — "
            "a bullet never mixes problem spaces",
        )
    ]


def validate_bundle_selection(
    bundle: PARBundle,
    selected_action_id: str,
    selected_result_id: str,
) -> list[BundleViolation]:
    """Both selected ids must be candidates of THIS bundle — or refuse.

    Selection in a bundle with no result candidates is unreachable by design:
    :func:`validate_result_presence` routes that bundle to the follow-up instead.
    """
    violations: list[BundleViolation] = []
    action_ids = {c.candidate_id for c in bundle.action_candidates}
    result_ids = {c.candidate_id for c in bundle.result_candidates}
    if selected_action_id not in action_ids:
        violations.append(
            BundleViolation(
                SELECTED_ACTION_OUTSIDE_BUNDLE,
                f"selected action {selected_action_id!r} is not a candidate of "
                f"bundle {bundle.bundle_id}",
            )
        )
    if selected_result_id not in result_ids:
        violations.append(
            BundleViolation(
                SELECTED_RESULT_OUTSIDE_BUNDLE,
                f"selected result {selected_result_id!r} is not a candidate of "
                f"bundle {bundle.bundle_id}",
            )
        )
    return violations


def validate_evidence_boundary(candidate: EvidenceBounded) -> list[BundleViolation]:
    """A component's cited evidence stays inside ONE problem space and ONE project.

    Reads the provenance stamps constructed at detection (spaces/entities of every
    cited claim), so a candidate assembled from claims across spaces — the LLM
    detection path later, or a bad persistence round-trip — is caught from the
    candidate alone. Empty stamps have nothing to check and pass.
    """
    violations: list[BundleViolation] = []
    foreign_spaces = set(candidate.evidence_problem_space_ids) - {candidate.problem_space_id}
    if foreign_spaces:
        violations.append(
            BundleViolation(
                CROSS_PROBLEM_SPACE_CONTAMINATION,
                f"{candidate.field_type.value} {candidate.text!r} cites evidence from "
                f"problem space(s) {sorted(foreign_spaces)} outside its own "
                f"({candidate.problem_space_id})",
            )
        )
    foreign_entities = set(candidate.evidence_experience_ids) - {candidate.experience_id}
    if foreign_entities:
        violations.append(
            BundleViolation(
                CROSS_PROJECT_CONTAMINATION,
                f"{candidate.field_type.value} {candidate.text!r} cites evidence from "
                f"experience(s) {sorted(foreign_entities)} outside its own "
                f"({candidate.experience_id})",
            )
        )
    return violations


def pairing_relationship(
    action_claim_ids: Sequence[int],
    result_claim_ids: Sequence[int],
    evidence_ids_by_claim: Mapping[int, AbstractSet[int]],
    *,
    result_attested: bool = False,
) -> str:
    """Derive the action→result relationship from provenance facts (§9.1) — never
    from problem-space membership or semantic similarity (§5.6.5-6).

    * ``direct`` — the selected action and result share a claim: the extractor found
      the work statement and the outcome in the same evidenced unit, under the
      coupling gate.
    * ``same_source_section`` — distinct claims whose cited evidence overlaps: the
      same source chunk narrates both.
    * ``user_attested`` — the result is the user's typed answer to this story's
      targeted question; selecting it IS the user's confirmation.
    * ``unknown`` — everything else. Unknown stays unknown.
    """
    if result_attested:
        return RELATIONSHIP_USER_ATTESTED
    if set(action_claim_ids) & set(result_claim_ids):
        return RELATIONSHIP_DIRECT
    action_evidence: set[int] = set()
    for claim_id in action_claim_ids:
        action_evidence |= set(evidence_ids_by_claim.get(claim_id, ()))
    result_evidence: set[int] = set()
    for claim_id in result_claim_ids:
        result_evidence |= set(evidence_ids_by_claim.get(claim_id, ()))
    if action_evidence & result_evidence:
        return RELATIONSHIP_SAME_SOURCE
    return RELATIONSHIP_UNKNOWN


def validate_pairing_support(
    action: ActionCandidate, result: ResultCandidate, relationship: str
) -> list[BundleViolation]:
    """The §9.2/§9.3 publication rule: an unsupported pairing is unselectable.

    Only direct, same-source, or user-attested relationships may back a bullet that
    implies causality. A pairing whose only connection is the shared problem space
    refuses with a machine-readable violation — the reviewer picks a supported
    result, attests the real outcome, or leaves the relationship honestly unknown.
    """
    if relationship in SUPPORTED_RELATIONSHIPS:
        return []
    return [
        BundleViolation(
            UNSUPPORTED_PAIRING,
            f"action {action.text!r} and result {result.text!r} share no source "
            "linkage (different claims, disjoint cited evidence) — a shared problem "
            "space is not proof of causality (§5.6.5); select a result the sources "
            "couple to this action, or attest the real outcome",
        )
    ]


def validate_result_presence(bundle: PARBundle) -> list[BundleViolation]:
    """A bundle must have result candidates to generate from — or it is a follow-up.

    The missing-result path never fills the slot: the violation names the next
    action (the targeted result-type question) and generation refuses.
    """
    if bundle.result_candidates:
        return []
    return [
        BundleViolation(
            MISSING_RESULT,
            f"bundle {bundle.bundle_id} has no result candidates — a result is "
            "asked for, never invented",
            next_action=ASK_TARGETED_FOLLOWUP,
        )
    ]


__all__ = [
    "ASK_TARGETED_FOLLOWUP",
    "CROSS_PROBLEM_SPACE_CONTAMINATION",
    "CROSS_PROJECT_CONTAMINATION",
    "FATAL_BUNDLE_CODES",
    "MISSING_RESULT",
    "PROBLEM_SPACE_MISMATCH",
    "RELATIONSHIP_DIRECT",
    "RELATIONSHIP_SAME_SOURCE",
    "RELATIONSHIP_UNKNOWN",
    "RELATIONSHIP_USER_ATTESTED",
    "SELECTED_ACTION_OUTSIDE_BUNDLE",
    "SELECTED_RESULT_OUTSIDE_BUNDLE",
    "SUPPORTED_RELATIONSHIPS",
    "UNSUPPORTED_PAIRING",
    "BundleViolation",
    "EvidenceBounded",
    "pairing_relationship",
    "validate_bundle_selection",
    "validate_evidence_boundary",
    "validate_pairing_support",
    "validate_problem_space_alignment",
    "validate_result_presence",
]
