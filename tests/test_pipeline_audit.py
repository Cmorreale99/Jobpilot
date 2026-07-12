"""End-to-end pipeline audit (hardening H8) — the V4 readiness gate's teeth.

§6 #31: a consistently-seeded corpus scores all-green through the real service.
§6 #32: one corrupted state per invariant fails ITS check and names the offending
rows — the tool can never report green over a broken spine.
"""

from __future__ import annotations

from app.domain.claims import (
    ASSIGNMENT_SECTION,
    SOURCE_DRIVE,
    SOURCE_USER_ATTESTATION,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ResultKind,
    ResultStatus,
    StorableClaim,
    StoredEvidence,
)
from app.domain.pipeline_audit import check_version_consistency
from app.domain.project_story import StoryAction, StoryContent, StoryResult, StoryReviewStatus
from app.domain.source_capture import INGESTION_OK, SourceElementInput
from app.domain.source_structure import STRUCTURER_VERSION, structure_source_text
from app.domain.text_normalization import normalize_source_text
from app.domain.validation_runs import KIND_PIPELINE_AUDIT
from app.services.claim_repository import InMemoryClaimRepository
from app.services.pipeline_audit import run_pipeline_audit
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.source_capture import InMemorySourceCaptureStore
from app.services.validation_run_log import InMemoryValidationRunLog

_USER = "u1"
_RAW = "# Cooper.ai\n\nCut nightly load failures from fourteen percent to zero.\n"
_OUTCOME = "Cut nightly load failures from fourteen percent to zero."


class _Seeded:
    """One fully-consistent spine: capture → elements → evidence → claim → story."""

    def __init__(self) -> None:
        self.repo = InMemoryClaimRepository()
        self.capture = InMemorySourceCaptureStore()
        self.stories = InMemoryProjectStoryRepository()
        self.log = InMemoryValidationRunLog()

        self.experience = self.repo.upsert_experience(
            _USER,
            ExperienceSeed(
                name="Cooper.ai",
                section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
                kind=ExperienceKind.EMPLOYER_ROLE,
            ),
        )

        self.version = self.capture.capture(
            _USER,
            source_type=SOURCE_DRIVE,
            source_ref="drv1",
            title="Doc",
            raw_text=_RAW,
            extractor="test",
        )
        elements = structure_source_text(_RAW)
        self.stored_elements = self.capture.record_elements(
            self.version.id,
            [
                SourceElementInput(
                    sequence_index=e.sequence_index,
                    element_type=e.element_type,
                    raw_start=e.raw_start,
                    raw_end=e.raw_end,
                    raw_text=e.raw_text,
                    normalized_text=normalize_source_text(e.raw_text),
                    level=e.level,
                    parent_index=e.parent_index,
                )
                for e in elements
            ],
            structurer_version=STRUCTURER_VERSION,
            ingestion_status=INGESTION_OK,
        )

        # One evidence chunk per element, raw-coordinate span refs (the H5 shape).
        self.chunks: list[EvidenceChunk] = []
        self.evidence: list[StoredEvidence] = []
        for element, stored in zip(elements, self.stored_elements, strict=True):
            chunk = EvidenceChunk(
                SOURCE_DRIVE,
                f"drv1#chars={element.raw_start}-{element.raw_end}",
                normalize_source_text(element.raw_text),
                element_id=stored.id,
                sequence_index=element.sequence_index,
            )
            row = self.repo.upsert_evidence(_USER, chunk)
            row = self.repo.assign_evidence(row.id, self.experience.id, method=ASSIGNMENT_SECTION)
            self.chunks.append(chunk)
            self.evidence.append(row)
        self.result_chunk = self.chunks[-1]
        self.result_row = self.evidence[-1]

        draft = DraftClaim(
            action_text="Rebuilt the nightly exporter with Python",
            action_tools=("Python",),
            result_text=_OUTCOME,
            result_kind=ResultKind.QUANTIFIED,
            evidence=(
                ClaimEvidenceRef(chunk=self.result_chunk, field=ClaimField.ACTION),
                ClaimEvidenceRef(
                    chunk=self.result_chunk, field=ClaimField.RESULT, outcome_quote=_OUTCOME
                ),
            ),
        )
        [self.claim] = self.repo.replace_unreviewed_claims(
            _USER,
            self.experience.id,
            [
                StorableClaim(
                    draft=draft,
                    status=ClaimStatus.APPROVED,
                    result_status=ResultStatus.UNVERIFIED,
                )
            ],
        )

        content = StoryContent(
            actions=(
                StoryAction(
                    component_id="a1",
                    summary=draft.action_text,
                    claim_ids=(self.claim.id,),
                ),
            ),
            results=(
                StoryResult(
                    component_id="r1",
                    text=_OUTCOME,
                    claim_ids=(self.claim.id,),
                    outcome_quote=_OUTCOME,
                ),
            ),
        )
        self.story = self.stories.upsert_draft(_USER, self.experience.id, content)
        # No evidenced Problem — attest one, the walk must resolve the attestation.
        self.repo.upsert_evidence(
            _USER,
            EvidenceChunk(
                SOURCE_USER_ATTESTATION,
                f"story:{self.story.id}:problem",
                "Nightly load failures cost four hours of manual recovery",
            ),
        )
        self.stories.transition_story(self.story.id, StoryReviewStatus.PENDING_REVIEW)
        self.stories.transition_story(self.story.id, StoryReviewStatus.APPROVED)

    def audit(self):
        return run_pipeline_audit(
            _USER, self.repo, self.capture, self.stories, validation_log=self.log
        )

    def check(self, name: str):
        return next(c for c in self.audit().checks if c.name == name)


def test_consistent_corpus_scores_all_green_and_records_the_row() -> None:
    seeded = _Seeded()
    scorecard = seeded.audit()
    assert scorecard.passed, scorecard.detail_lines()
    assert {c.name for c in scorecard.checks} == {
        "capture",
        "element_coverage",
        "ownership_labeled",
        "active_orphans",
        "reviewed_on_stale",
        "provenance_walk",
        "version_consistency",
    }
    assert all(c.checked > 0 for c in scorecard.checks), "every check saw real rows"
    [run] = seeded.log.list_runs(_USER, KIND_PIPELINE_AUDIT)
    assert run.passed and any("capture: PASS" in line for line in run.detail)


# --- negative controls (§6 #32): each corruption fails ITS check, names its rows -------


def test_uncaptured_evidence_fails_capture() -> None:
    seeded = _Seeded()
    ghost = seeded.repo.upsert_evidence(
        _USER, EvidenceChunk(SOURCE_DRIVE, "ghost#chars=0-5", "ghost")
    )
    seeded.repo.assign_evidence(ghost.id, seeded.experience.id, method=ASSIGNMENT_SECTION)
    check = seeded.check("capture")
    assert not check.passed
    assert any(
        f"evidence {ghost.id}" in f and "no active captured version" in f for f in check.failures
    )


def test_coverage_gap_fails_element_coverage() -> None:
    seeded = _Seeded()
    # Re-record the tree WITHOUT the paragraph element: its characters are now
    # silently unaccounted for — exactly what the coverage invariant forbids.
    elements = structure_source_text(_RAW)
    heading = elements[0]
    seeded.capture.record_elements(
        seeded.version.id,
        [
            SourceElementInput(
                sequence_index=heading.sequence_index,
                element_type=heading.element_type,
                raw_start=heading.raw_start,
                raw_end=heading.raw_end,
                raw_text=heading.raw_text,
                normalized_text=normalize_source_text(heading.raw_text),
                level=heading.level,
                parent_index=None,
            )
        ],
        structurer_version=STRUCTURER_VERSION,
        ingestion_status=INGESTION_OK,
    )
    check = seeded.check("element_coverage")
    assert not check.passed
    assert any("uncovered" in f for f in check.failures)


def test_unlabeled_assignment_fails_ownership() -> None:
    seeded = _Seeded()
    row = seeded.result_row
    seeded.repo.assign_evidence(row.id, seeded.experience.id, method=None)
    check = seeded.check("ownership_labeled")
    assert not check.passed
    assert any(f"evidence {row.id}" in f and "no assignment_method" in f for f in check.failures)


def test_stale_active_row_fails_active_orphans() -> None:
    seeded = _Seeded()
    tampered = EvidenceChunk(
        seeded.result_chunk.source_type,
        seeded.result_chunk.source_ref,
        "Text the raw payload never produced.",
        element_id=seeded.result_chunk.element_id,
        sequence_index=seeded.result_chunk.sequence_index,
    )
    seeded.repo.upsert_evidence(_USER, tampered)
    check = seeded.check("active_orphans")
    assert not check.passed
    assert any(
        f"evidence {seeded.result_row.id}" in f and "not recomputable" in f for f in check.failures
    )


def test_reviewed_claim_on_superseded_evidence_fails_reviewed_on_stale() -> None:
    seeded = _Seeded()
    seeded.repo.supersede_evidence(seeded.result_row.id, None)
    check = seeded.check("reviewed_on_stale")
    assert not check.passed
    assert any(
        f"claim {seeded.claim.id}" in f and "superseded evidence" in f for f in check.failures
    )


def test_broken_walk_fails_provenance() -> None:
    seeded = _Seeded()
    other = seeded.repo.upsert_experience(
        _USER,
        ExperienceSeed(
            name="Other Project",
            section=ExperienceSection.PROJECTS_HACKATHONS,
            kind=ExperienceKind.PROJECT,
        ),
    )
    broken = seeded.stories.upsert_draft(
        _USER,
        other.id,
        StoryContent(
            actions=(StoryAction(component_id="a1", summary="Did work", claim_ids=(999,)),),
        ),
    )
    seeded.stories.transition_story(broken.id, StoryReviewStatus.PENDING_REVIEW)
    seeded.stories.transition_story(broken.id, StoryReviewStatus.APPROVED)
    check = seeded.check("provenance_walk")
    assert not check.passed
    assert any("claim 999 does not exist" in f for f in check.failures)


def test_upsert_stamps_missing_normalization_version_on_returning_ref() -> None:
    """A pre-versioning row whose ref the current run reproduces verbatim gets
    stamped — the current normalizer just vouched for the text (the live gate's
    130-row legacy class)."""
    from dataclasses import replace

    from app.domain.text_normalization import NORMALIZATION_VERSION

    repo = InMemoryClaimRepository()
    chunk = EvidenceChunk(SOURCE_DRIVE, "legacy#chars=0-4", "text")
    row = repo.upsert_evidence(_USER, chunk)
    repo._evidence[row.id] = replace(row, normalization_version=None)  # legacy shape

    again = repo.upsert_evidence(_USER, chunk)
    assert again.id == row.id
    assert again.normalization_version == NORMALIZATION_VERSION


def test_version_drift_fails_version_consistency() -> None:
    stale = StoredEvidence(
        id=1,
        user_id=_USER,
        source_type=SOURCE_DRIVE,
        source_ref="drv1#chars=0-10",
        chunk_text="text",
        normalization_version=None,  # pre-versioning row still active
        experience_id=3,
        assignment_method=ASSIGNMENT_SECTION,
    )
    check = check_version_consistency([stale], documents=())
    assert not check.passed
    assert any("normalization_version None" in f for f in check.failures)
