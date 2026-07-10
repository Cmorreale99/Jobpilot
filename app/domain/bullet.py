"""Single-bullet generation: the v3.1 selection flow's terminal step (pure).

One bundle, one selected action, one selected result → exactly one resume bullet,
composed **verbatim** from the selected candidates (never authored) and hard-gated
through all four bundle validators plus the number gate before a character is
emitted. Any violation refuses generation outright (:class:`BulletGenerationError`
carries the machine-readable findings); a bundle with no result candidates is the
targeted follow-up's job, never a slot to fill (T-missing-result).

The caller resolves ``evidence_texts`` — the chunk texts (or attestations) cited by
the selected candidates' claims — because grounding must run against provenance,
never against other generated text (T14). With no evidence texts, any number in the
bullet is unsupported and generation refuses: the gate fails closed.

Pure logic: no imports from ``integrations/`` implementations or ``llm/``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.bundle_validation import (
    BundleViolation,
    validate_bundle_selection,
    validate_evidence_boundary,
    validate_problem_space_alignment,
    validate_result_presence,
)
from app.domain.problem_space import PARBundle
from app.domain.project_story import unsupported_number_tokens

# Bullet-level violation code (mirrors the story layer's structural code): a number in
# the composed bullet that appears in none of the cited evidence texts.
UNSUPPORTED_NUMBER = "unsupported_number"


class BulletGenerationError(ValueError):
    """Generation refused — one or more validator findings; nothing was composed."""

    def __init__(self, violations: Sequence[BundleViolation]) -> None:
        super().__init__("; ".join(str(v) for v in violations))
        self.violations = tuple(violations)


@dataclass(frozen=True)
class GeneratedBullet:
    """One generated resume bullet with its full trace (bullet → candidates → claims)."""

    text: str
    problem_space_id: str
    bundle_id: str
    action_candidate_id: str
    result_candidate_id: str
    claim_ids: tuple[int, ...]


def _compose(action_text: str, result_text: str) -> str:
    """The deterministic composition: selected action — selected result, verbatim.

    Mirrors the tailorer's impact line ("action — result"): no rewriting, no
    invention — only trailing-period normalization so the join reads as one bullet.
    """
    action = action_text.strip().rstrip(".")
    result = result_text.strip().rstrip(".")
    return f"{action} — {result}."


def generate_bullet(
    bundle: PARBundle,
    selected_action_id: str,
    selected_result_id: str,
    *,
    evidence_texts: Sequence[str] = (),
) -> GeneratedBullet:
    """Compose the one bullet the selection defines — or refuse.

    Gate order: result presence (a missing result is a follow-up, not an error to
    push through), selection membership (both ids inside THIS bundle), problem-space
    alignment across problem/action/result, the evidence boundary on each component,
    then the number gate over the composed text. All findings accumulate past the
    membership gate so the caller sees every problem at once.
    """
    presence = validate_result_presence(bundle)
    if presence:
        raise BulletGenerationError(presence)

    membership = validate_bundle_selection(bundle, selected_action_id, selected_result_id)
    if membership:
        raise BulletGenerationError(membership)

    action = next(c for c in bundle.action_candidates if c.candidate_id == selected_action_id)
    result = next(c for c in bundle.result_candidates if c.candidate_id == selected_result_id)

    violations = list(validate_problem_space_alignment(bundle.problem, action, result))
    for candidate in (bundle.problem, action, result):
        violations.extend(validate_evidence_boundary(candidate))

    text = _compose(action.text, result.text)
    for token in unsupported_number_tokens(text, list(evidence_texts)):
        violations.append(
            BundleViolation(
                UNSUPPORTED_NUMBER,
                f"bullet states {token!r}, which appears in no cited evidence text or attestation",
            )
        )
    if violations:
        raise BulletGenerationError(violations)

    return GeneratedBullet(
        text=text,
        problem_space_id=bundle.problem_space_id,
        bundle_id=bundle.bundle_id,
        action_candidate_id=action.candidate_id,
        result_candidate_id=result.candidate_id,
        claim_ids=tuple(sorted({*action.claim_ids, *result.claim_ids})),
    )


__all__ = [
    "UNSUPPORTED_NUMBER",
    "BulletGenerationError",
    "GeneratedBullet",
    "generate_bullet",
]
