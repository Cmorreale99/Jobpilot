"""V3 Phase 4 — the full story loop, end to end over the live-shaped corpus.

synthesis (heuristic, offline) → review (attest a Problem, approve through the gate,
exclude with a reason) → render the Master CV docx from the approved stories. The
assertions are on the real docx XML: approved stories render, everything else (pending,
excluded, incomplete) does not, and two approved stories sharing an outcome span refuse
the render (the final-CV duplicate-metric enforcement that existed nowhere before V3).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest
from app.config import Settings
from app.domain.artifacts import KIND_MASTER_CV_DOCX
from app.domain.resume_context import ResumeProfile
from app.domain.story_snapshot import DuplicateMetricError
from app.services.artifact_store import InMemoryArtifactStore
from app.services.master_cv_render import render_master_cv_from_stories
from app.services.master_cv_snapshot import InMemorySnapshotStore
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_review import approve_story, attest_story_component, exclude_story
from app.services.story_synthesis import run_story_synthesis

from tests.live_corpus import SHARED_OUTCOME_QUOTE, build_live_corpus

PROFILE_PATH = Path(__file__).parent / "fixtures" / "render" / "profile.json"
TEMPLATE = Path(__file__).parent.parent / "templates" / "resume_template.docx"


def _profile() -> ResumeProfile:
    return ResumeProfile.from_dict(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        resume_template_path=str(TEMPLATE),
        resume_profile_path=str(PROFILE_PATH),
        artifacts_dir=str(tmp_path / "artifacts"),
    )


def _docx_text(path: Path) -> str:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return re.sub(r"<[^>]+>", "|", xml)


def _story_id(stories: InMemoryProjectStoryRepository, corpus, name: str) -> int:  # type: ignore[no-untyped-def]
    story = stories.get_story_for_experience(corpus.user_id, corpus.entity(name).id)
    assert story is not None
    return story.id


def test_full_v3_loop_renders_only_approved_stories(tmp_path: Path) -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()

    # 1. Synthesis: one pending_review draft per confirmed entity.
    report = run_story_synthesis(corpus.user_id, corpus.repository, stories)
    assert len(report.synthesized) == 15

    # 2. Review. MassDEP is evidenced-complete → approve straight through the gate.
    approve_story(stories, corpus.repository, _story_id(stories, corpus, "MassDEP"))

    # The engine has an evidenced Result but only code-bug 'Problems' → attest one, then approve.
    engine_story = _story_id(stories, corpus, "investment-decision-workflow-engine")
    attest_story_component(
        stories,
        corpus.repository,
        engine_story,
        "problem",
        "Analysts screened investment candidates by hand every quarter",
    )
    approve_story(stories, corpus.repository, engine_story)

    # OneWorld is complete (the shared-quote Result) — approve it alone (portfolio stays pending).
    approve_story(stories, corpus.repository, _story_id(stories, corpus, "OneWorld"))

    # Cooper.ai: excluded with a retained reason — never renders.
    exclude_story(stories, _story_id(stories, corpus, "Cooper.ai"), "portfolio inventory")

    # 3. Render the Master CV from the approved stories.
    artifact = render_master_cv_from_stories(
        corpus.user_id,
        stories,
        corpus.repository,
        InMemorySnapshotStore(),
        InMemoryArtifactStore(),
        _settings(tmp_path),
        profile=_profile(),
    )

    docx = Path(artifact.file_path)
    assert docx.exists()
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    assert "{{" not in xml and "{%" not in xml  # renderer fidelity: no unrendered tags
    assert "Times New Roman" in xml

    text = _docx_text(docx)
    # Approved stories render (topics keep entities distinguishable in the live fixture).
    assert "permit" in text  # MassDEP
    assert "screening" in text  # engine
    assert SHARED_OUTCOME_QUOTE in text  # OneWorld's Result
    # The attested Problem gated the engine story but is never itself a bullet.
    assert "screened investment candidates by hand" not in text
    # Everything not approved is absent.
    assert "notes" not in text  # personal-knowledge-os (pending)
    assert "ratings" not in text  # BoardGameGeek (pending)
    assert "onboarding" not in text  # Cooper.ai (excluded)

    assert artifact.kind == KIND_MASTER_CV_DOCX
    assert artifact.master_cv_version == 1


def test_full_v3_loop_refuses_a_cross_story_duplicate_metric(tmp_path: Path) -> None:
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    run_story_synthesis(corpus.user_id, corpus.repository, stories)

    # OneWorld and the portfolio entity both cite "Top 3 out of 100+ teams" — approving both
    # makes the CV double-count one achievement across two projects.
    approve_story(stories, corpus.repository, _story_id(stories, corpus, "OneWorld"))
    approve_story(
        stories, corpus.repository, _story_id(stories, corpus, "cameron-morreale-portfolio")
    )

    with pytest.raises(DuplicateMetricError):
        render_master_cv_from_stories(
            corpus.user_id,
            stories,
            corpus.repository,
            InMemorySnapshotStore(),
            InMemoryArtifactStore(),
            _settings(tmp_path),
            profile=_profile(),
        )


def test_merged_entity_story_is_invalidated_and_never_renders(tmp_path: Path) -> None:
    """T4: a merged entity gets no story in the CV — invalidation drops it from approved."""
    corpus = build_live_corpus()
    stories = InMemoryProjectStoryRepository()
    run_story_synthesis(corpus.user_id, corpus.repository, stories)

    massdep = corpus.entity("MassDEP")
    approve_story(stories, corpus.repository, _story_id(stories, corpus, "MassDEP"))
    approve_story(stories, corpus.repository, _story_id(stories, corpus, "OneWorld"))

    # A roster merge/discard cascades into invalidation → back to draft, no longer approved.
    stories.invalidate_story(massdep.id, reason="merged into another entity")

    artifact = render_master_cv_from_stories(
        corpus.user_id,
        stories,
        corpus.repository,
        InMemorySnapshotStore(),
        InMemoryArtifactStore(),
        _settings(tmp_path),
        profile=_profile(),
    )
    text = _docx_text(Path(artifact.file_path))
    assert "permit" not in text  # MassDEP invalidated → dropped
    assert SHARED_OUTCOME_QUOTE in text  # OneWorld still approved → renders
