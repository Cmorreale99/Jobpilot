"""Pipeline audit service: assemble persisted state, run the H8 checks, record the row.

The I/O half of the V4 readiness gate: reads the whole spine for one user (evidence,
claims, approved stories, captured documents + versions + elements), hands it to the
pure checks (``domain/pipeline_audit.py``), and records the scorecard in
``validation_runs`` (kind ``pipeline_audit``) — the gate's sign-off artifact. Offline,
deterministic, zero LLM spend.
"""

from __future__ import annotations

import logging

from app.domain.claims import (
    SOURCE_USER_ATTESTATION,
    ClaimRepository,
    StoredEvidence,
    split_span_ref,
)
from app.domain.pipeline_audit import (
    AuditScorecard,
    CapturedDocumentState,
    run_audit_checks,
)
from app.domain.project_story import ProjectStoryRepository, StoryReviewStatus
from app.domain.source_capture import (
    CapturedSourceVersion,
    SourceCaptureStore,
    StoredSourceElement,
)
from app.domain.validation_runs import KIND_PIPELINE_AUDIT, ValidationRunLog

logger = logging.getLogger(__name__)


def run_pipeline_audit(
    user_id: str,
    repository: ClaimRepository,
    capture_store: SourceCaptureStore,
    story_repository: ProjectStoryRepository,
    *,
    validation_log: ValidationRunLog | None = None,
) -> AuditScorecard:
    """Audit one user's pipeline state against every V4-gate invariant."""
    evidence = repository.list_all_evidence(user_id)
    claims = repository.list_claims(user_id)
    stories = story_repository.list_stories(user_id, status=StoryReviewStatus.APPROVED)

    documents: list[CapturedDocumentState] = []
    versions_by_ref: dict[tuple[str, str], CapturedSourceVersion | None] = {}
    elements_by_id: dict[int, StoredSourceElement] = {}
    for document in capture_store.list_documents(user_id):
        version = capture_store.get_active_version(
            user_id, document.source_type, document.source_ref
        )
        elements = tuple(capture_store.list_elements(version.id)) if version is not None else ()
        documents.append(
            CapturedDocumentState(document=document, version=version, elements=elements)
        )
        versions_by_ref[(document.source_type, document.source_ref)] = version
        for element in elements:
            elements_by_id[element.id] = element
    # Evidence refs not covered by the captured-document enumeration (never captured)
    # must still resolve to None explicitly so check_capture names them.
    for row in evidence:
        if row.source_type == SOURCE_USER_ATTESTATION:
            continue
        base_ref, _ = split_span_ref(row.source_ref)
        versions_by_ref.setdefault((row.source_type, base_ref), None)

    attestations_by_ref: dict[str, StoredEvidence] = {
        row.source_ref: row for row in evidence if row.source_type == SOURCE_USER_ATTESTATION
    }

    scorecard = run_audit_checks(
        evidence=evidence,
        claims=claims,
        stories=stories,
        experiences=repository.list_experiences(user_id),
        documents=documents,
        versions_by_ref=versions_by_ref,
        elements_by_id=elements_by_id,
        attestations_by_ref=attestations_by_ref,
    )
    if validation_log is not None:
        validation_log.record(
            user_id,
            KIND_PIPELINE_AUDIT,
            subject_ref="pipeline_audit",
            passed=scorecard.passed,
            detail=tuple(scorecard.detail_lines()),
        )
    logger.info(
        "pipeline audit for %s: %s (%s)",
        user_id,
        "PASS" if scorecard.passed else "FAIL",
        ", ".join(f"{c.name}={'ok' if c.passed else len(c.failures)}" for c in scorecard.checks),
    )
    return scorecard


__all__ = ["run_pipeline_audit"]
