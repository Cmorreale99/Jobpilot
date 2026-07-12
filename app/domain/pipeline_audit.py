"""End-to-end pipeline reconciliation: the V4 readiness gate's checks (hardening H8).

Every invariant the hardening arc built is asserted here, offline and zero-LLM, over
the persisted state alone (PIPELINE_HARDENING_PLAN.md §7):

* **capture** — every active source-derived evidence row's base ref has an active
  captured version with raw text (H2: nothing feeds the pipeline without a stored
  original).
* **element_coverage** — every active document version's element tree accounts for
  every character of its raw text, every element carries a disposition, and the
  version's reconciliation status is ``ok`` (H4: structure is source data, losses
  are visible).
* **ownership_labeled** — every active assigned evidence row records HOW it was
  assigned (H1: a human correction is never bitwise-indistinguishable from a guess).
* **active_orphans** — every active row's chunk text is recomputable from the active
  version's raw payload (H3/H6: the active set IS the current chunking; a span that
  no longer reproduces its text is a stale row that should have been superseded).
* **reviewed_on_stale** — no APPROVED claim cites superseded evidence (H6: approved
  claims feed downstream artifacts and must stand on live text; rejections are
  terminal, keep their decided text in-row, and stay visible in the standing queue
  without holding the gate).
* **provenance_walk** — for every approved story, component → claim → claim_evidence
  → evidence (→ element → version) closes, result quotes locate in their cited
  chunks, and attestation-backed components resolve to their attestation rows.
* **version_consistency** — active rows and versions are stamped with the current
  ``NORMALIZATION_VERSION``/``STRUCTURER_VERSION`` (H3: derivations are versioned).
* **entity_coverage** — every CONFIRMED roster entity holds at least one active
  evidence chunk, and every confirmed entity with evidence has at least one claim.
  Provenance integrity is not completeness: a confirmed project whose source text was
  captured but swallowed by a neighboring chunk (the Paper-recommender case), or whose
  evidence extraction never processed, is a silent hole in the output — the gate
  names it instead of passing around it.

Pure and deterministic — no I/O; ``services/pipeline_audit.py`` assembles the data and
records the scorecard, ``python -m app.tools.audit_pipeline`` is the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimField,
    ClaimStatus,
    Experience,
    ExperienceStatus,
    StoredEvidence,
    split_span_ref,
)
from app.domain.source_capture import (
    INGESTION_OK,
    CapturedSourceDocument,
    CapturedSourceVersion,
    StoredSourceElement,
)
from app.domain.source_structure import (
    STRUCTURER_VERSION,
    SourceElement,
    verify_full_coverage,
)
from app.domain.text_normalization import NORMALIZATION_VERSION, normalize_source_text

if TYPE_CHECKING:
    from app.domain.project_story import ProjectStory

# How many offending rows a check names in its detail lines — the count is always
# exact; the naming is capped so a systemic failure doesn't flood the scorecard.
MAX_NAMED_FAILURES = 20


@dataclass(frozen=True)
class AuditCheck:
    """One invariant's verdict: what was checked, and every named violation."""

    name: str
    checked: int
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.failures

    def detail_lines(self) -> list[str]:
        verdict = "PASS" if self.passed else f"FAIL ({len(self.failures)})"
        lines = [f"{self.name}: {verdict} (checked={self.checked})"]
        lines.extend(f"  {f}" for f in self.failures[:MAX_NAMED_FAILURES])
        if len(self.failures) > MAX_NAMED_FAILURES:
            lines.append(f"  ... and {len(self.failures) - MAX_NAMED_FAILURES} more")
        return lines


@dataclass(frozen=True)
class AuditScorecard:
    """The full audit verdict — the V4 readiness gate's sign-off shape."""

    checks: tuple[AuditCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def detail_lines(self) -> list[str]:
        return [line for check in self.checks for line in check.detail_lines()]


@dataclass(frozen=True)
class CapturedDocumentState:
    """One captured document with its active version and that version's elements."""

    document: CapturedSourceDocument
    version: CapturedSourceVersion | None
    elements: tuple[StoredSourceElement, ...] = ()


def _norm(text: str) -> str:
    return " ".join(text.split())


def _is_source_row(row: StoredEvidence) -> bool:
    return row.source_type != SOURCE_USER_ATTESTATION


def _active_source_rows(evidence: Sequence[StoredEvidence]) -> list[StoredEvidence]:
    return [row for row in evidence if row.is_active and _is_source_row(row)]


def check_capture(
    evidence: Sequence[StoredEvidence],
    versions_by_ref: Mapping[tuple[str, str], CapturedSourceVersion | None],
) -> AuditCheck:
    """H2: every active source-derived row is backed by a stored raw original."""
    failures: list[str] = []
    rows = _active_source_rows(evidence)
    for row in rows:
        base_ref, _ = split_span_ref(row.source_ref)
        version = versions_by_ref.get((row.source_type, base_ref))
        if version is None:
            failures.append(
                f"evidence {row.id} ({row.source_type} {base_ref}): no active captured version"
            )
        elif not version.raw_text:
            failures.append(
                f"evidence {row.id} ({row.source_type} {base_ref}): "
                f"active version {version.id} has empty raw text"
            )
    return AuditCheck(name="capture", checked=len(rows), failures=tuple(failures))


def check_element_coverage(documents: Sequence[CapturedDocumentState]) -> AuditCheck:
    """H4: every active version's tree covers its raw text; every element disposed."""
    failures: list[str] = []
    checked = 0
    for state in documents:
        version = state.version
        if version is None:
            continue  # a document with no active version backs no evidence; capture covers it
        checked += 1
        label = f"{state.document.source_type} {state.document.source_ref} (version {version.id})"
        if version.ingestion_status != INGESTION_OK:
            failures.append(f"{label}: ingestion_status={version.ingestion_status!r}")
        if not state.elements:
            if version.raw_text.strip():
                failures.append(f"{label}: no elements recorded for non-empty raw text")
            continue
        for element in state.elements:
            if not element.extraction_status:
                failures.append(f"{label}: element {element.id} has no disposition")
        reconstructed = [
            SourceElement(
                sequence_index=e.sequence_index,
                element_type=e.element_type,
                raw_start=e.raw_start,
                raw_end=e.raw_end,
                raw_text=version.raw_text[e.raw_start : e.raw_end],
                level=e.level,
            )
            for e in sorted(state.elements, key=lambda e: e.sequence_index)
        ]
        for violation in verify_full_coverage(version.raw_text, reconstructed):
            failures.append(f"{label}: {violation}")
    return AuditCheck(name="element_coverage", checked=checked, failures=tuple(failures))


def check_ownership_labeled(evidence: Sequence[StoredEvidence]) -> AuditCheck:
    """H1: every active assigned row says HOW it was assigned."""
    failures: list[str] = []
    rows = [row for row in _active_source_rows(evidence) if row.experience_id is not None]
    for row in rows:
        if not row.assignment_method:
            failures.append(
                f"evidence {row.id} ({row.source_ref}): assigned to experience "
                f"{row.experience_id} with no assignment_method"
            )
    return AuditCheck(name="ownership_labeled", checked=len(rows), failures=tuple(failures))


def _recomputable(row: StoredEvidence, raw: str) -> bool:
    """Does the active version's raw payload reproduce this row's chunk text?

    Structured rows (H5) carry raw-coordinate spans: ``normalize(raw[a:b])`` is the
    chunk text. Flat-era rows carry spans into the normalized text. Spanless rows
    (commits, whole-document chunks) must appear within the normalized payload.
    All comparisons are whitespace-collapsed (the normalizer's own tolerance).
    """
    target = _norm(row.chunk_text)
    if not target:
        return False
    _, span = split_span_ref(row.source_ref)
    if span is not None:
        start, end = span
        if end <= len(raw) and _norm(normalize_source_text(raw[start:end])) == target:
            return True
        normalized = normalize_source_text(raw)
        return end <= len(normalized) and _norm(normalized[start:end]) == target
    return target in _norm(normalize_source_text(raw))


def check_active_orphans(
    evidence: Sequence[StoredEvidence],
    versions_by_ref: Mapping[tuple[str, str], CapturedSourceVersion | None],
    elements_by_id: Mapping[int, StoredSourceElement],
) -> AuditCheck:
    """H6: the active set IS the current chunking — every active row recomputes."""
    failures: list[str] = []
    rows = _active_source_rows(evidence)
    for row in rows:
        base_ref, _ = split_span_ref(row.source_ref)
        version = versions_by_ref.get((row.source_type, base_ref))
        if version is None:
            continue  # named by check_capture; not double-counted here
        if row.element_id is not None:
            element = elements_by_id.get(row.element_id)
            if element is None:
                failures.append(
                    f"evidence {row.id} ({row.source_ref}): element {row.element_id} does not exist"
                )
                continue
            if element.document_version_id != version.id:
                failures.append(
                    f"evidence {row.id} ({row.source_ref}): element {row.element_id} "
                    f"belongs to version {element.document_version_id}, active is {version.id}"
                )
                continue
        if not _recomputable(row, version.raw_text):
            failures.append(
                f"evidence {row.id} ({row.source_ref}): chunk text is not recomputable "
                "from the active version's raw payload — stale row never superseded"
            )
    return AuditCheck(name="active_orphans", checked=len(rows), failures=tuple(failures))


def check_reviewed_on_stale(
    claims: Sequence[Claim], evidence_by_id: Mapping[int, StoredEvidence]
) -> AuditCheck:
    """H6: an APPROVED claim standing on vanished text blocks the gate.

    Approved only: an approved claim feeds downstream artifacts and must stand on
    live evidence. A REJECTED claim is a terminal decision that retains its decided
    text in-row and feeds nothing — the H6 standing queue still surfaces it for
    human awareness, but it cannot hold the gate hostage forever.
    """
    failures: list[str] = []
    reviewed = [c for c in claims if c.status is ClaimStatus.APPROVED]
    for claim in reviewed:
        stale = sorted(
            {
                link.evidence_id
                for link in claim.evidence
                if (row := evidence_by_id.get(link.evidence_id)) is not None and not row.is_active
            }
        )
        if stale:
            failures.append(
                f"claim {claim.id} ({claim.status.value}) cites superseded evidence "
                f"{', '.join(str(i) for i in stale)} — resolve via the "
                "superseded-reviewed queue"
            )
    return AuditCheck(name="reviewed_on_stale", checked=len(reviewed), failures=tuple(failures))


def check_provenance_walk(
    stories: Sequence[ProjectStory],
    claims_by_id: Mapping[int, Claim],
    evidence_by_id: Mapping[int, StoredEvidence],
    attestations_by_ref: Mapping[str, StoredEvidence],
    elements_by_id: Mapping[int, StoredSourceElement],
) -> AuditCheck:
    """§7.6: story component → claim → claim_evidence → evidence closes, quotes locate."""
    failures: list[str] = []

    def walk_claims(story: ProjectStory, component: str, claim_ids: Sequence[int]) -> list[Claim]:
        resolved: list[Claim] = []
        for claim_id in claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                failures.append(f"story {story.id} {component}: claim {claim_id} does not exist")
                continue
            if claim.experience_id != story.experience_id:
                failures.append(
                    f"story {story.id} {component}: claim {claim_id} belongs to experience "
                    f"{claim.experience_id}, story is {story.experience_id}"
                )
                continue
            for link in claim.evidence:
                stored = evidence_by_id.get(link.evidence_id)
                if stored is None:
                    failures.append(
                        f"story {story.id} {component}: claim {claim_id} links evidence "
                        f"{link.evidence_id} which does not exist"
                    )
                elif stored.element_id is not None and stored.element_id not in elements_by_id:
                    failures.append(
                        f"story {story.id} {component}: evidence {stored.id} links element "
                        f"{stored.element_id} which does not exist"
                    )
            resolved.append(claim)
        return resolved

    for story in stories:
        content = story.content
        if content.problem_refs:
            walk_claims(story, "problem", content.problem_refs)
        elif (content.problem_text or "").strip() == "":
            ref = f"story:{story.id}:problem"
            if ref not in attestations_by_ref:
                failures.append(
                    f"story {story.id} problem: no evidenced refs and no attestation ({ref})"
                )
        for action in content.actions:
            walk_claims(story, f"action {action.component_id}", action.claim_ids)
        evidenced_result = False
        for result in content.results:
            resolved = walk_claims(story, f"result {result.component_id}", result.claim_ids)
            if resolved:
                evidenced_result = True
            if result.outcome_quote:
                cited = " ".join(
                    _norm(evidence_by_id[link.evidence_id].chunk_text)
                    for claim in resolved
                    for link in claim.evidence
                    if link.field is ClaimField.RESULT and link.evidence_id in evidence_by_id
                )
                if _norm(result.outcome_quote) not in cited:
                    failures.append(
                        f"story {story.id} result {result.component_id}: outcome_quote "
                        "does not locate in any cited result chunk"
                    )
        if not evidenced_result:
            ref = f"story:{story.id}:result"
            if ref not in attestations_by_ref:
                failures.append(
                    f"story {story.id} result: no evidenced Result and no attestation ({ref})"
                )
    return AuditCheck(name="provenance_walk", checked=len(stories), failures=tuple(failures))


def check_version_consistency(
    evidence: Sequence[StoredEvidence], documents: Sequence[CapturedDocumentState]
) -> AuditCheck:
    """H3: every active row/version is stamped with the current generation."""
    failures: list[str] = []
    rows = _active_source_rows(evidence)
    for row in rows:
        if row.normalization_version != NORMALIZATION_VERSION:
            failures.append(
                f"evidence {row.id} ({row.source_ref}): normalization_version "
                f"{row.normalization_version} != current {NORMALIZATION_VERSION}"
            )
    versions = [s.version for s in documents if s.version is not None]
    for version in versions:
        if version.structurer_version != STRUCTURER_VERSION:
            failures.append(
                f"version {version.id}: structurer_version {version.structurer_version} "
                f"!= current {STRUCTURER_VERSION}"
            )
    return AuditCheck(
        name="version_consistency",
        checked=len(rows) + len(versions),
        failures=tuple(failures),
    )


def check_entity_coverage(
    experiences: Sequence[Experience],
    evidence: Sequence[StoredEvidence],
    claims: Sequence[Claim],
) -> AuditCheck:
    """Completeness, not just integrity: every confirmed entity is fed and processed.

    A confirmed roster entity is a human decision that a real-world project belongs
    in the output. One with zero active evidence means its source text exists but the
    interpretation layer never routed a single chunk to it (captured-but-swallowed —
    the Paper-recommender failure). One with evidence but zero claims means extraction
    never ran for it or produced nothing (the JobPilot failure). Both read as "done"
    on every integrity check while a whole project is silently absent downstream.
    """
    failures: list[str] = []
    confirmed = [e for e in experiences if e.status is ExperienceStatus.CONFIRMED]
    chunk_counts: dict[int, int] = {}
    for row in _active_source_rows(evidence):
        if row.experience_id is not None:
            chunk_counts[row.experience_id] = chunk_counts.get(row.experience_id, 0) + 1
    claim_counts: dict[int, int] = {}
    for claim in claims:
        claim_counts[claim.experience_id] = claim_counts.get(claim.experience_id, 0) + 1
    for entity in confirmed:
        chunks = chunk_counts.get(entity.id, 0)
        if chunks == 0:
            failures.append(
                f"experience {entity.id} ({entity.name!r}): confirmed with ZERO active "
                "evidence chunks — its text was captured but never assigned to it "
                "(chunk-boundary swallow or assignment gap)"
            )
        elif claim_counts.get(entity.id, 0) == 0:
            failures.append(
                f"experience {entity.id} ({entity.name!r}): {chunks} active evidence "
                "chunk(s) but ZERO claims — extraction never ran for this entity or "
                "produced nothing"
            )
    return AuditCheck(name="entity_coverage", checked=len(confirmed), failures=tuple(failures))


def run_audit_checks(
    *,
    evidence: Sequence[StoredEvidence],
    claims: Sequence[Claim],
    stories: Sequence[ProjectStory],
    documents: Sequence[CapturedDocumentState],
    versions_by_ref: Mapping[tuple[str, str], CapturedSourceVersion | None],
    elements_by_id: Mapping[int, StoredSourceElement],
    attestations_by_ref: Mapping[str, StoredEvidence],
    experiences: Sequence[Experience] = (),
) -> AuditScorecard:
    """Run every gate check over assembled state — pure, ordered, complete."""
    claims_by_id = {c.id: c for c in claims}
    evidence_by_id = {e.id: e for e in evidence}
    return AuditScorecard(
        checks=(
            check_capture(evidence, versions_by_ref),
            check_element_coverage(documents),
            check_ownership_labeled(evidence),
            check_active_orphans(evidence, versions_by_ref, elements_by_id),
            check_reviewed_on_stale(claims, evidence_by_id),
            check_provenance_walk(
                stories, claims_by_id, evidence_by_id, attestations_by_ref, elements_by_id
            ),
            check_version_consistency(evidence, documents),
            check_entity_coverage(experiences, evidence, claims),
        )
    )


__all__ = [
    "MAX_NAMED_FAILURES",
    "AuditCheck",
    "AuditScorecard",
    "CapturedDocumentState",
    "check_active_orphans",
    "check_capture",
    "check_element_coverage",
    "check_entity_coverage",
    "check_ownership_labeled",
    "check_provenance_walk",
    "check_reviewed_on_stale",
    "check_version_consistency",
    "run_audit_checks",
]
