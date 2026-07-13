"""Master CV rendering orchestration: approved claims -> snapshot -> docx artifact.

The wiring around the frozen renderer (``app/render/render_master_cv.py``):

1. Snapshot the approved claims (idempotent — unchanged content reuses the version).
   The docx is a *rendered view of a version*, so every artifact traces to one.
2. Build the renderer's exact JSON contract from the profile + the same approved
   claims (``domain/resume_context.py``).
3. Write the context JSON and call the renderer as-is; its fidelity assertions
   (no unrendered tags, all text runs Times New Roman) run on every render.
4. Record the artifact row (``kind=master_cv_docx``), upserted per version.

The claim query here is ``status=approved`` — the same enforcement point as the
snapshot service. A pending or rejected claim cannot reach the docx.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.domain.artifacts import KIND_MASTER_CV_DOCX, Artifact, ArtifactStore
from app.domain.claims import ClaimRepository, ClaimStatus
from app.domain.master_cv_snapshot import MasterCvSnapshotStore, StoredSnapshot
from app.domain.project_story import ProjectStoryRepository
from app.domain.resume_context import (
    ResumeProfile,
    build_resume_context,
    build_story_resume_context,
)
from app.domain.validation_runs import ValidationRunLog
from app.render.render_master_cv import render
from app.services.master_cv_snapshot import create_master_cv_snapshot
from app.services.story_snapshot import create_story_snapshot

logger = logging.getLogger(__name__)


class RenderConfigurationError(RuntimeError):
    """Raised when rendering is requested without a usable profile or template."""


def load_resume_profile(settings: Settings) -> ResumeProfile:
    """Load and validate the profile JSON; fail loudly rather than invent a header."""
    if not settings.resume_profile_path:
        raise RenderConfigurationError(
            "RESUME_PROFILE_PATH is not set. Rendering needs the user's profile "
            "(name/tagline/contact/education/skills); JobPilot never invents it."
        )
    path = Path(settings.resume_profile_path)
    if not path.exists():
        raise RenderConfigurationError(f"resume profile not found at {path}")
    return ResumeProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))


def render_master_cv_artifact(
    user_id: str,
    claim_repository: ClaimRepository,
    snapshot_store: MasterCvSnapshotStore,
    artifact_store: ArtifactStore,
    settings: Settings | None = None,
    *,
    profile: ResumeProfile | None = None,
) -> Artifact:
    """Render the Master CV docx from approved claims and record the artifact row."""
    settings = settings or get_settings()
    template = Path(settings.resume_template_path)
    if not template.exists():
        raise RenderConfigurationError(f"resume template not found at {template}")
    profile = profile or load_resume_profile(settings)

    # One version per distinct approved-claims content; the docx renders THAT version.
    snapshot = create_master_cv_snapshot(user_id, claim_repository, snapshot_store)

    experiences = claim_repository.list_experiences(user_id)
    approved = claim_repository.list_claims(user_id, status=ClaimStatus.APPROVED)
    context = build_resume_context(profile, experiences, approved)

    return _emit(user_id, context, snapshot, settings, template, artifact_store)


def render_master_cv_from_stories(
    user_id: str,
    story_repository: ProjectStoryRepository,
    claim_repository: ClaimRepository,
    snapshot_store: MasterCvSnapshotStore,
    artifact_store: ArtifactStore,
    settings: Settings | None = None,
    *,
    profile: ResumeProfile | None = None,
    validation_log: ValidationRunLog | None = None,
) -> Artifact:
    """Render the Master CV docx from **approved project stories** (V3 Phase 4).

    The V3 render unit is the story: ``create_story_snapshot`` stamps a story-shaped version
    (render-time resume-ready gate + duplicate-metric refusal applied there), and the docx
    renders that frozen version through the same frozen template. Raises
    :class:`~app.domain.story_snapshot.DuplicateMetricError` when two approved stories share
    an outcome span. With a ``validation_log``, required-source failures in the latest
    gather block publication (:class:`~app.services.story_snapshot.SourceCompletenessError`)
    and every story's publication disposition is recorded (MASTER CV REPAIR §5.9/§13.8).
    """
    settings = settings or get_settings()
    template = Path(settings.resume_template_path)
    if not template.exists():
        raise RenderConfigurationError(f"resume template not found at {template}")
    profile = profile or load_resume_profile(settings)

    snapshot = create_story_snapshot(
        user_id,
        story_repository,
        claim_repository,
        snapshot_store,
        validation_log=validation_log,
    )
    context = build_story_resume_context(profile, snapshot.content)

    return _emit(user_id, context, snapshot, settings, template, artifact_store)


def _emit(
    user_id: str,
    context: dict[str, Any],
    snapshot: StoredSnapshot,
    settings: Settings,
    template: Path,
    artifact_store: ArtifactStore,
) -> Artifact:
    """Write the renderer context + docx for one version and record the artifact row."""
    out_dir = Path(settings.artifacts_dir) / user_id
    out_dir.mkdir(parents=True, exist_ok=True)
    context_path = out_dir / f"master_cv_v{snapshot.version}.json"
    docx_path = out_dir / f"master_cv_v{snapshot.version}.docx"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    # The frozen renderer runs its own fidelity assertions on the output.
    render(context_path, docx_path, template)

    artifact = artifact_store.record(
        user_id,
        KIND_MASTER_CV_DOCX,
        str(docx_path),
        master_cv_version=snapshot.version,
    )
    logger.info("rendered Master CV v%d for %s -> %s", snapshot.version, user_id, docx_path)
    return artifact
