"""M11 exit tests: the frozen renderer wired to approved claims.

* Renderer fidelity assertions pass on real output (no unrendered tags, all text
  runs Times New Roman) — the renderer itself enforces them on every render.
* A pending_review claim in the DB does not appear in the rendered docx.
* The section picker changes which heading an experience renders under.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest
from app.config import Settings
from app.domain.artifacts import KIND_MASTER_CV_DOCX
from app.domain.claims import (
    SOURCE_DRIVE,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceSection,
    ExperienceSeed,
    ResultKind,
    ResultStatus,
    StorableClaim,
)
from app.domain.resume_context import (
    ResumeProfile,
    ResumeProfileError,
    build_resume_context,
    render_claim_bullet,
)
from app.services.artifact_store import InMemoryArtifactStore
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_render import (
    RenderConfigurationError,
    render_master_cv_artifact,
)
from app.services.master_cv_snapshot import InMemorySnapshotStore

PROFILE_PATH = Path(__file__).parent / "fixtures" / "render" / "profile.json"
CONTRACT_FIXTURE = Path(__file__).parent / "fixtures" / "fixture_master_cv.json"
TEMPLATE = Path(__file__).parent.parent / "templates" / "resume_template.docx"

_CHUNK = EvidenceChunk(SOURCE_DRIVE, "drv1", "Automated triage with Python. Cut effort by 90%.")


def _profile() -> ResumeProfile:
    return ResumeProfile.from_dict(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        resume_template_path=str(TEMPLATE),
        resume_profile_path=str(PROFILE_PATH),
        artifacts_dir=str(tmp_path / "artifacts"),
    )


def _storable(action: str, *, result: str | None = None) -> StorableClaim:
    return StorableClaim(
        draft=DraftClaim(
            action_text=action,
            action_tools=("Python",),
            result_text=result,
            result_kind=ResultKind.QUANTIFIED if result else ResultKind.MISSING,
            result_metric_json={"resolves": "time", "metric_text": result} if result else None,
            evidence=(ClaimEvidenceRef(chunk=_CHUNK, field=ClaimField.ACTION),),
        ),
        status=ClaimStatus.PENDING_REVIEW,
        result_status=ResultStatus.UNVERIFIED,
    )


def _seed_experience(
    repo: InMemoryClaimRepository,
    name: str,
    section: ExperienceSection,
    actions: tuple[str, ...],
    *,
    subtitle: str | None = None,
    dates: str | None = None,
) -> tuple[int, list[int]]:
    experience = repo.upsert_experience(
        "u1", ExperienceSeed(name=name, section=section, subtitle=subtitle, dates=dates)
    )
    inserted = repo.replace_unreviewed_claims("u1", experience.id, [_storable(a) for a in actions])
    return experience.id, [c.id for c in inserted]


def _docx_text(path: Path) -> str:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return re.sub(r"<[^>]+>", "|", xml)


# --- the contract ------------------------------------------------------------------------


def test_context_matches_the_fixture_contract_exactly() -> None:
    fixture = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    repo = InMemoryClaimRepository()
    _, claim_ids = _seed_experience(
        repo,
        "Wellington Management",
        ExperienceSection.PROFESSIONAL_EXPERIENCE,
        ("Reconstructed the data architecture in Python",),
        subtitle="Data Architecture & Engineering Intern | Boston, MA",
        dates="Jun-Aug 2025",
    )
    repo.transition_claim(claim_ids[0], ClaimStatus.APPROVED)

    context = build_resume_context(_profile(), repo.list_experiences("u1"), repo.list_claims("u1"))

    assert set(context) == set(fixture)  # same top-level keys, nothing more or less
    entry = context["experiences"][0]
    assert set(entry) == set(fixture["experiences"][0])  # name/subtitle/dates/bullets
    assert set(context["skills"][0]) == set(fixture["skills"][0])
    assert "skill_list" in context["skills"][0]  # the gotcha: NOT "items"
    assert set(context["education"][0]) == set(fixture["education"][0])


def test_profile_rejects_the_items_key() -> None:
    with pytest.raises(ResumeProfileError, match="skill_list"):
        ResumeProfile.from_dict(
            {"name": "X", "skills": [{"name": "Languages", "items": ["Python"]}]}
        )


def test_bullets_derive_from_action_and_result_verbatim() -> None:
    repo = InMemoryClaimRepository()
    experience_id, claim_ids = _seed_experience(
        repo, "carrier-etl", ExperienceSection.PROJECTS_HACKATHONS, ()
    )
    inserted = repo.replace_unreviewed_claims(
        "u1",
        experience_id,
        [
            _storable(
                "Automated exception triage with Python",
                result="Cut weekly effort from 10 hours to 1 hour",
            )
        ],
    )
    claim = repo.transition_claim(inserted[0].id, ClaimStatus.APPROVED)
    assert render_claim_bullet(claim) == (
        "Automated exception triage with Python. Cut weekly effort from 10 hours to 1 hour."
    )


def test_none_subtitle_and_dates_render_as_empty_strings() -> None:
    repo = InMemoryClaimRepository()
    _, claim_ids = _seed_experience(
        repo, "carrier-etl", ExperienceSection.PROJECTS_HACKATHONS, ("Built the ETL in Python",)
    )
    repo.transition_claim(claim_ids[0], ClaimStatus.APPROVED)
    context = build_resume_context(_profile(), repo.list_experiences("u1"), repo.list_claims("u1"))
    entry = context["projects_and_hackathons"][0]
    assert entry["subtitle"] == ""  # never the string "None" in the docx
    assert entry["dates"] == ""


# --- EXIT TEST: fidelity + pending exclusion -----------------------------------------------


def test_render_passes_fidelity_and_excludes_pending_claims(tmp_path: Path) -> None:
    repo = InMemoryClaimRepository()
    experience_id, claim_ids = _seed_experience(
        repo,
        "Wellington Management",
        ExperienceSection.PROFESSIONAL_EXPERIENCE,
        (
            "Reconstructed the data architecture in Python",
            "Rewrote the settlement importer in Python",
        ),
    )
    approved_id, pending_id = claim_ids
    repo.transition_claim(approved_id, ClaimStatus.APPROVED)
    # pending_id stays pending_review — it must not reach the docx.

    artifact = render_master_cv_artifact(
        "u1",
        repo,
        InMemorySnapshotStore(),
        InMemoryArtifactStore(),
        _settings(tmp_path),
    )

    docx = Path(artifact.file_path)
    assert docx.exists()
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    # Fidelity ran inside render(); assert the core invariants independently too.
    assert "{{" not in xml and "{%" not in xml
    assert "Times New Roman" in xml

    text = _docx_text(docx)
    assert "Reconstructed the data architecture in Python" in text
    assert "Rewrote the settlement importer" not in text  # EXIT: pending never renders
    assert artifact.master_cv_version == 1
    assert artifact.kind == KIND_MASTER_CV_DOCX


# --- EXIT TEST: section picker changes the rendered heading ---------------------------------


def test_section_picker_changes_the_rendered_heading(tmp_path: Path) -> None:
    repo = InMemoryClaimRepository()
    experience_id, claim_ids = _seed_experience(
        repo, "carrier-etl", ExperienceSection.PROJECTS_HACKATHONS, ("Built the ETL in Python",)
    )
    repo.transition_claim(claim_ids[0], ClaimStatus.APPROVED)
    snapshots = InMemorySnapshotStore()
    artifacts = InMemoryArtifactStore()
    settings = _settings(tmp_path)

    before = render_master_cv_artifact("u1", repo, snapshots, artifacts, settings)
    text = _docx_text(Path(before.file_path))
    assert text.index("carrier-etl") > text.index("PROJECTS AND HACKATHONS")

    repo.update_experience_layout(experience_id, section=ExperienceSection.PROFESSIONAL_EXPERIENCE)
    after = render_master_cv_artifact("u1", repo, snapshots, artifacts, settings)
    text = _docx_text(Path(after.file_path))
    # EXIT: the experience now renders under PROFESSIONAL EXPERIENCE, not PROJECTS.
    assert text.index("PROFESSIONAL EXPERIENCE") < text.index("carrier-etl")
    assert text.index("carrier-etl") < text.index("PROJECTS AND HACKATHONS")
    assert after.master_cv_version == before.master_cv_version + 1  # a new version


# --- artifacts + configuration guards --------------------------------------------------------


def test_rerender_of_unchanged_content_reuses_the_version_row(tmp_path: Path) -> None:
    repo = InMemoryClaimRepository()
    _, claim_ids = _seed_experience(
        repo, "carrier-etl", ExperienceSection.PROJECTS_HACKATHONS, ("Built the ETL in Python",)
    )
    repo.transition_claim(claim_ids[0], ClaimStatus.APPROVED)
    snapshots = InMemorySnapshotStore()
    artifacts = InMemoryArtifactStore()
    settings = _settings(tmp_path)

    first = render_master_cv_artifact("u1", repo, snapshots, artifacts, settings)
    second = render_master_cv_artifact("u1", repo, snapshots, artifacts, settings)
    assert first.master_cv_version == second.master_cv_version == 1
    assert len(artifacts.list_artifacts("u1")) == 1  # upserted, not duplicated


def test_missing_profile_fails_loudly_never_invents(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"resume_profile_path": ""})
    with pytest.raises(RenderConfigurationError, match="RESUME_PROFILE_PATH"):
        render_master_cv_artifact(
            "u1",
            InMemoryClaimRepository(),
            InMemorySnapshotStore(),
            InMemoryArtifactStore(),
            settings,
        )
