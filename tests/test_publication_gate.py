"""Publication gating (MASTER CV REPAIR §5.9/§14.1/§14.5, §16.16).

Source truth: a Master CV candidate publishes only when required sources are complete.
A failed required source (README/CLAUDE.md unreadable, tree unenumerable) marks the
candidate incomplete and BLOCKS publication — the prior valid version survives
untouched. Every story that an otherwise-successful publication drops (ungrounded
number, not resume-ready) is a recorded, queryable disposition — never only a log line.
"""

from __future__ import annotations

import pytest
from app.domain.validation_runs import (
    KIND_MASTER_CV_PUBLICATION,
    KIND_SOURCE_GATHER,
)
from app.services.claim_repository import InMemoryClaimRepository
from app.services.master_cv_snapshot import InMemorySnapshotStore
from app.services.project_story_repository import InMemoryProjectStoryRepository
from app.services.story_snapshot import SourceCompletenessError, create_story_snapshot
from app.services.validation_run_log import InMemoryValidationRunLog

from tests.conftest import seed_approved_story

USER = "u1"


def _repos() -> tuple[InMemoryClaimRepository, InMemoryProjectStoryRepository]:
    return InMemoryClaimRepository(), InMemoryProjectStoryRepository()


def test_failed_required_source_blocks_publication_and_preserves_prior_state() -> None:
    claims, stories = _repos()
    store = InMemorySnapshotStore()
    log = InMemoryValidationRunLog()
    seed_approved_story(claims, stories, user_id=USER)

    # A prior valid version exists.
    prior = create_story_snapshot(USER, stories, claims, store)
    assert store.get_latest(USER) is not None

    # The most recent gather recorded a required-source failure.
    log.record(
        USER,
        KIND_SOURCE_GATHER,
        subject_ref="sources",
        passed=False,
        detail=("required_failure: cmorreale/broken-readme: README present but not captured",),
    )
    seed_approved_story(claims, stories, user_id=USER, name="Newline", topic="ingest")

    with pytest.raises(SourceCompletenessError, match="broken-readme"):
        create_story_snapshot(USER, stories, claims, store, validation_log=log)

    # The prior valid Master CV survives the failed candidate untouched (§14.5).
    latest = store.get_latest(USER)
    assert latest is not None
    assert latest.version == prior.version
    assert latest.content == prior.content


def test_passing_gather_allows_publication() -> None:
    claims, stories = _repos()
    store = InMemorySnapshotStore()
    log = InMemoryValidationRunLog()
    log.record(USER, KIND_SOURCE_GATHER, subject_ref="sources", passed=True)
    seed_approved_story(claims, stories, user_id=USER)

    snapshot = create_story_snapshot(USER, stories, claims, store, validation_log=log)
    assert snapshot.version >= 1


def test_newer_successful_gather_supersedes_an_old_failure() -> None:
    claims, stories = _repos()
    store = InMemorySnapshotStore()
    log = InMemoryValidationRunLog()
    log.record(
        USER,
        KIND_SOURCE_GATHER,
        subject_ref="sources",
        passed=False,
        detail=("required_failure: x: README present but not captured",),
    )
    log.record(USER, KIND_SOURCE_GATHER, subject_ref="sources", passed=True)
    seed_approved_story(claims, stories, user_id=USER)

    snapshot = create_story_snapshot(USER, stories, claims, store, validation_log=log)
    assert snapshot.version >= 1


def test_no_gather_history_does_not_block() -> None:
    claims, stories = _repos()
    store = InMemorySnapshotStore()
    log = InMemoryValidationRunLog()
    seed_approved_story(claims, stories, user_id=USER)

    snapshot = create_story_snapshot(USER, stories, claims, store, validation_log=log)
    assert snapshot.version >= 1


def test_publication_records_story_dispositions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every approved story's fate at publication is a recorded disposition (§13.8)."""
    claims, stories = _repos()
    store = InMemorySnapshotStore()
    log = InMemoryValidationRunLog()
    seed_approved_story(claims, stories, user_id=USER)

    create_story_snapshot(USER, stories, claims, store, validation_log=log)

    runs = log.list_runs(USER, KIND_MASTER_CV_PUBLICATION)
    assert runs, "publication recorded no disposition run"
    assert runs[-1].passed is True
    assert any("rendered" in line for line in runs[-1].detail)
