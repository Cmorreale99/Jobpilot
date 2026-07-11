"""Canonical source capture + loud gather (hardening H2).

The acceptance criteria under test: every gathered source's **as-received** text is
durable before normalization (raw fidelity, idempotent by content hash, immutable
version history), and every discovered candidate ends in exactly one recorded
disposition — a source that failed to read or was policy-excluded is never silently
indistinguishable from an empty one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.config import Settings
from app.db.base import Base
from app.db.session import create_session_factory
from app.db.source_capture_store import SqlSourceCaptureStore
from app.domain.claims import SOURCE_DRIVE, SOURCE_GITHUB_COMMIT, SOURCE_GITHUB_README
from app.domain.source_capture import (
    SourceCaptureStore,
    SourceElementInput,
    raw_content_hash,
)
from app.domain.source_structure import STRUCTURER_VERSION
from app.domain.text_normalization import normalize_source_text
from app.domain.validation_runs import KIND_SOURCE_GATHER
from app.integrations.base import (
    DriveDocument,
    DriveResponseError,
    DriveSource,
    GitHubCommit,
    GitHubDocument,
    GitHubRepo,
    UploadCandidate,
    UploadDocument,
)
from app.services.roster import (
    GATHER_OK,
    GATHER_POLICY_EXCLUDED,
    GATHER_READ_FAILED,
    gather_source_documents,
)
from app.services.source_capture import InMemorySourceCaptureStore
from app.services.validation_run_log import InMemoryValidationRunLog

# --- store semantics, against both implementations -----------------------------------


@pytest.fixture(params=["in_memory", "sql"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> SourceCaptureStore:
    if request.param == "in_memory":
        return InMemorySourceCaptureStore()
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'capture.db'}")
    Base.metadata.create_all(engine)
    return SqlSourceCaptureStore(create_session_factory(engine))


def _capture(store: SourceCaptureStore, raw: str, *, title: str = "Resume"):
    return store.capture(
        "u1",
        source_type=SOURCE_DRIVE,
        source_ref="drv1",
        title=title,
        raw_text=raw,
        extractor="drive:FakeDriveClient",
    )


def test_capture_is_idempotent_by_content_hash(store: SourceCaptureStore) -> None:
    first = _capture(store, "raw body")
    second = _capture(store, "raw body")
    assert second.id == first.id
    assert second.content_hash == raw_content_hash("raw body")
    assert len(store.list_versions("u1", SOURCE_DRIVE, "drv1")) == 1


def test_changed_content_creates_new_active_version_and_keeps_old_raw(
    store: SourceCaptureStore,
) -> None:
    first = _capture(store, "old body")
    second = _capture(store, "new body")
    assert second.id != first.id
    assert second.is_active

    versions = store.list_versions("u1", SOURCE_DRIVE, "drv1")
    assert [v.is_active for v in versions] == [False, True]
    # The old payload is immutable history, not overwritten.
    assert versions[0].raw_text == "old body"
    active = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert active is not None and active.raw_text == "new body"


def test_content_returning_to_an_earlier_hash_reactivates_that_version(
    store: SourceCaptureStore,
) -> None:
    first = _capture(store, "body A")
    _capture(store, "body B")
    third = _capture(store, "body A")
    assert third.id == first.id  # no duplicate payload row
    versions = store.list_versions("u1", SOURCE_DRIVE, "drv1")
    assert len(versions) == 2
    assert [v.is_active for v in versions] == [True, False]


def test_capture_refreshes_document_metadata(store: SourceCaptureStore) -> None:
    _capture(store, "raw body", title="Old title")
    _capture(store, "raw body", title="New title")
    document = store.get_document("u1", SOURCE_DRIVE, "drv1")
    assert document is not None and document.title == "New title"


def test_record_elements_resolves_parents_and_stamps_the_version(
    store: SourceCaptureStore,
) -> None:
    raw = "# Cooper.ai\n\nDuplicate rows overstated charges."
    version = _capture(store, raw)
    inputs = [
        SourceElementInput(
            sequence_index=0,
            element_type="heading",
            raw_start=0,
            raw_end=11,
            raw_text=raw[0:11],
            normalized_text="# Cooper.ai",
            level=1,
        ),
        SourceElementInput(
            sequence_index=1,
            element_type="paragraph",
            raw_start=13,
            raw_end=len(raw),
            raw_text=raw[13:],
            normalized_text=raw[13:],
            parent_index=0,
        ),
    ]
    stored = store.record_elements(version.id, inputs, structurer_version=1, ingestion_status="ok")
    assert stored[1].parent_id == stored[0].id  # hierarchy survives persistence
    listed = store.list_elements(version.id)
    assert [(e.sequence_index, e.element_type, e.parent_id) for e in listed] == [
        (0, "heading", None),
        (1, "paragraph", stored[0].id),
    ]
    refreshed = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert refreshed is not None
    assert refreshed.structurer_version == 1
    assert refreshed.ingestion_status == "ok"

    # Elements are a pure derivation: re-recording replaces, never accumulates.
    store.record_elements(version.id, inputs[:1], structurer_version=2, ingestion_status="ok")
    assert len(store.list_elements(version.id)) == 1
    refreshed = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert refreshed is not None and refreshed.structurer_version == 2


# --- gather: raw fidelity + disposition accounting ------------------------------------

# Word-per-line PDF pathology: normalization reflows it, so raw != normalized — the
# fidelity test can prove capture happens BEFORE normalization.
_MANGLED = "Rebuilt\n \nthe\n \nexporter\n \nwith\n \nPython."


@dataclass
class FakeDriveClient:
    sources: list[DriveSource] = field(default_factory=list)
    documents: dict[str, DriveDocument] = field(default_factory=dict)
    fail_refs: set[str] = field(default_factory=set)

    async def list_candidate_sources(self, user_id: str) -> list[DriveSource]:
        return list(self.sources)

    async def read_source(self, source_ref: str) -> DriveDocument:
        if source_ref in self.fail_refs:
            raise DriveResponseError(f"unreadable payload for {source_ref}")
        return self.documents[source_ref]


@dataclass
class FakeGitHubClient:
    repos: list[GitHubRepo] = field(default_factory=list)
    readmes: dict[str, GitHubDocument] = field(default_factory=dict)
    commits: dict[str, list[GitHubCommit]] = field(default_factory=dict)

    async def list_candidate_repos(self, user_id: str) -> list[GitHubRepo]:
        return list(self.repos)

    async def read_repo(self, repo_ref: str) -> GitHubDocument:
        return self.readmes[repo_ref]

    async def list_commits(self, repo_ref: str) -> list[GitHubCommit]:
        return self.commits.get(repo_ref, [])


@dataclass
class FakeUploadsClient:
    candidates: list[UploadCandidate] = field(default_factory=list)
    documents: dict[str, UploadDocument] = field(default_factory=dict)
    fail_refs: set[str] = field(default_factory=set)

    async def list_candidate_uploads(self) -> list[UploadCandidate]:
        return list(self.candidates)

    async def read_upload(self, upload_ref: str) -> UploadDocument:
        if upload_ref in self.fail_refs:
            raise FileNotFoundError(f"No uploaded file named {upload_ref!r}.")
        return self.documents[upload_ref]


def _settings() -> Settings:
    return Settings(
        gdrive_source_folder_id="career_docs",
        github_username="jordanrivera",
    )


def _drive_source(ref: str, *, folder: str = "career_docs", mime: str = "text/plain"):
    return DriveSource(source_ref=ref, title=f"Doc {ref}", mime_type=mime, folder_id=folder)


def _drive_doc(ref: str, text: str) -> DriveDocument:
    return DriveDocument(source_ref=ref, title=f"Doc {ref}", mime_type="text/plain", text=text)


async def test_gather_persists_raw_pre_normalization_text() -> None:
    """Acceptance: the as-received text is recoverable, byte-identical, pre-normalization."""
    store = InMemorySourceCaptureStore()
    drive = FakeDriveClient(
        sources=[_drive_source("drv1")], documents={"drv1": _drive_doc("drv1", _MANGLED)}
    )
    gathered = await gather_source_documents(
        drive, FakeGitHubClient(), "u1", _settings(), capture_store=store
    )

    (document,) = gathered.documents
    active = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert active is not None
    assert active.raw_text == _MANGLED  # exactly as the client returned it
    assert document.text == normalize_source_text(_MANGLED)
    assert document.text != active.raw_text  # capture happened BEFORE normalization
    assert normalize_source_text(active.raw_text) == document.text  # and derives it


async def test_gather_records_element_trees_for_every_captured_version() -> None:
    """H4 acceptance: every active version gets a reconciled, hierarchy-carrying
    element tree — headings own their content; commits are one atomic element."""
    store = InMemorySourceCaptureStore()
    markdown = "# Cooper.ai\n\nDuplicate FedEx rows overstated charges.\n"
    drive = FakeDriveClient(
        sources=[_drive_source("drv1")], documents={"drv1": _drive_doc("drv1", markdown)}
    )
    repo = GitHubRepo(repo_ref="jordanrivera/carrier-etl", name="carrier-etl", owner="jordanrivera")
    github = FakeGitHubClient(
        repos=[repo],
        readmes={repo.repo_ref: GitHubDocument(repo.repo_ref, "carrier-etl", "# Carrier ETL")},
        commits={repo.repo_ref: [GitHubCommit(repo.repo_ref, "abc123", "Fix the dedupe job")]},
    )
    await gather_source_documents(drive, github, "u1", _settings(), capture_store=store)

    version = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert version is not None
    assert version.structurer_version == STRUCTURER_VERSION
    assert version.ingestion_status == "ok"
    elements = store.list_elements(version.id)
    assert [(e.element_type, e.parent_id) for e in elements] == [
        ("heading", None),
        ("paragraph", elements[0].id),  # the paragraph is OWNED by its heading
    ]
    assert markdown[elements[1].raw_start : elements[1].raw_end].startswith("Duplicate FedEx")

    commit_version = store.get_active_version("u1", SOURCE_GITHUB_COMMIT, f"{repo.repo_ref}@abc123")
    assert commit_version is not None and commit_version.ingestion_status == "ok"
    (commit_element,) = store.list_elements(commit_version.id)
    assert commit_element.element_type == "commit_message"


async def test_regather_skips_restructuring_an_unchanged_version() -> None:
    store = InMemorySourceCaptureStore()
    drive = FakeDriveClient(
        sources=[_drive_source("drv1")], documents={"drv1": _drive_doc("drv1", "Same body.")}
    )
    await gather_source_documents(drive, FakeGitHubClient(), "u1", _settings(), capture_store=store)
    version = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert version is not None
    first_ids = [e.id for e in store.list_elements(version.id)]

    await gather_source_documents(drive, FakeGitHubClient(), "u1", _settings(), capture_store=store)
    assert [e.id for e in store.list_elements(version.id)] == first_ids  # not re-derived


async def test_gather_captures_github_readme_and_commits() -> None:
    store = InMemorySourceCaptureStore()
    repo = GitHubRepo(repo_ref="jordanrivera/carrier-etl", name="carrier-etl", owner="jordanrivera")
    github = FakeGitHubClient(
        repos=[repo],
        readmes={repo.repo_ref: GitHubDocument(repo.repo_ref, "carrier-etl", "# Carrier ETL")},
        commits={repo.repo_ref: [GitHubCommit(repo.repo_ref, "abc123", "Fix the dedupe job")]},
    )
    await gather_source_documents(FakeDriveClient(), github, "u1", _settings(), capture_store=store)

    readme = store.get_active_version("u1", SOURCE_GITHUB_README, repo.repo_ref)
    commit = store.get_active_version("u1", SOURCE_GITHUB_COMMIT, f"{repo.repo_ref}@abc123")
    assert readme is not None and readme.raw_text == "# Carrier ETL"
    assert commit is not None and commit.raw_text == "Fix the dedupe job"


async def test_every_candidate_gets_exactly_one_disposition() -> None:
    """Acceptance: silently_dropped_sources = 0 — gathered, excluded, or failed."""
    drive = FakeDriveClient(
        sources=[
            _drive_source("ok_doc"),
            _drive_source("broken_doc"),
            _drive_source("spreadsheet", mime="application/vnd.ms-excel"),
            _drive_source("elsewhere", folder="personal_stuff"),
        ],
        documents={"ok_doc": _drive_doc("ok_doc", "Shipped the exporter with Python.")},
        fail_refs={"broken_doc"},
    )
    gathered = await gather_source_documents(drive, FakeGitHubClient(), "u1", _settings())

    report = gathered.report
    assert len(report.ok) == len(gathered.documents) == 1
    assert {d.source_ref for d in report.read_failed} == {"broken_doc"}
    excluded = {d.source_ref: d.reason for d in report.policy_excluded}
    assert excluded == {
        "spreadsheet": "mime_not_allowed:application/vnd.ms-excel",
        "elsewhere": "out_of_scope",
    }
    # One failing source never takes the others down with it.
    assert gathered.documents[0].source_ref == "ok_doc"


async def test_uploads_read_failure_is_recorded_not_fatal() -> None:
    """Symmetry: an uploads read error used to abort the whole gather (F6)."""
    uploads = FakeUploadsClient(
        candidates=[
            UploadCandidate(upload_ref="gone.md", title="gone", mime_type="text/markdown"),
            UploadCandidate(upload_ref="award.txt", title="award", mime_type="text/plain"),
            UploadCandidate(upload_ref="scan.pdf", title="scan", mime_type="application/pdf"),
        ],
        documents={
            "award.txt": UploadDocument(
                upload_ref="award.txt", title="award", mime_type="text/plain", text="Won the award."
            )
        },
        fail_refs={"gone.md"},
    )
    gathered = await gather_source_documents(
        FakeDriveClient(), FakeGitHubClient(), "u1", _settings(), uploads_client=uploads
    )

    assert [d.source_ref for d in gathered.documents] == ["award.txt"]
    assert {d.source_ref for d in gathered.report.read_failed} == {"gone.md"}
    assert {d.source_ref for d in gathered.report.policy_excluded} == {"scan.pdf"}


async def test_gather_records_a_validation_run() -> None:
    log = InMemoryValidationRunLog()
    drive = FakeDriveClient(
        sources=[_drive_source("ok_doc"), _drive_source("broken_doc")],
        documents={"ok_doc": _drive_doc("ok_doc", "Shipped it.")},
        fail_refs={"broken_doc"},
    )
    await gather_source_documents(drive, FakeGitHubClient(), "u1", _settings(), validation_log=log)

    (run,) = log.list_runs("u1", KIND_SOURCE_GATHER)
    assert run.passed is False  # a read failure fails the pass
    assert any("read_failed" in line and "broken_doc" in line for line in run.detail)


async def test_every_evidence_row_has_recoverable_pre_normalization_text() -> None:
    """The H2 acceptance criterion, end to end over the reality-shaped roster fixtures:
    for every persisted evidence row, the pre-normalization source text is recoverable
    from the capture store, and the chunk text derives from it via the normalizer."""
    from app.domain.claims import ExperienceStatus, split_span_ref
    from app.integrations.mock.drive import MockDriveClient
    from app.integrations.mock.github import MockGitHubClient
    from app.services.claim_repository import InMemoryClaimRepository
    from app.services.roster import run_roster_assignment, run_roster_detection

    fixtures = Path(__file__).parent / "fixtures" / "roster"
    settings = Settings(
        gdrive_source_folder_id="career_docs",
        gdrive_mock_fixtures_dir=str(fixtures / "drive"),
        github_username="jordanrivera",
        github_mock_fixtures_dir=str(fixtures / "github"),
    )
    repo = InMemoryClaimRepository()
    store = InMemorySourceCaptureStore()
    drive = MockDriveClient(fixtures / "drive")
    github = MockGitHubClient(fixtures / "github")

    await run_roster_detection(drive, github, "u1", repo, settings, capture_store=store)
    for experience in repo.list_experiences("u1"):
        repo.set_experience_status(experience.id, ExperienceStatus.CONFIRMED)
    report = await run_roster_assignment(drive, github, "u1", repo, settings, capture_store=store)
    assert report.chunks > 0

    rows = list(repo.list_unassigned_evidence("u1"))
    for experience in repo.list_experiences("u1"):
        rows.extend(repo.list_assigned_evidence("u1", experience.id))
    assert rows
    for row in rows:
        base_ref, span = split_span_ref(row.source_ref)
        active = store.get_active_version("u1", row.source_type, base_ref)
        assert active is not None, f"no captured raw for {row.source_type}:{base_ref}"
        # H3 recomputability: normalize(raw) reproduces the exact text the stored
        # span points into — not just containment, slice equality.
        normalized = normalize_source_text(active.raw_text)
        if span is not None:
            start, end = span
            assert normalized[start:end] == row.chunk_text
        else:
            assert row.chunk_text == normalized  # whole-document evidence (commits)
        assert active.normalization_version == row.normalization_version


async def test_gather_report_statuses_cover_every_disposition() -> None:
    drive = FakeDriveClient(
        sources=[_drive_source("ok_doc")],
        documents={"ok_doc": _drive_doc("ok_doc", "Shipped it.")},
    )
    gathered = await gather_source_documents(drive, FakeGitHubClient(), "u1", _settings())
    assert {d.status for d in gathered.report.dispositions} <= {
        GATHER_OK,
        GATHER_READ_FAILED,
        GATHER_POLICY_EXCLUDED,
    }
    assert gathered.report.summary() == {
        GATHER_OK: 1,
        GATHER_READ_FAILED: 0,
        GATHER_POLICY_EXCLUDED: 0,
    }
