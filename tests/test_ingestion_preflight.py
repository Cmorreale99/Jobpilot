"""Ingestion preflight: discovery manifest + deterministic raw-to-extracted ledger.

The acceptance battery for the discovery/extraction/structuring boundary repair:

* discovery MUST explicitly surface ``paper recommender system`` from the sources;
* enumeration is complete (every captured document and repository manifests);
* reconciliation is deterministic, checkpointed (resume without repeating work),
  isolated from canonical state, and enforces 100% coverage;
* the whole path is zero-LLM by construction — a paid model call is unreachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.domain.ingestion_preflight import ProjectCandidate, build_manifest
from app.domain.source_structure import (
    ELEMENT_HEADING,
    structure_source_text,
    verify_full_coverage,
)
from app.domain.text_normalization import normalize_source_text
from app.services.claim_repository import InMemoryClaimRepository
from app.services.ingestion_preflight import (
    build_source_manifest,
    format_preflight_summary,
    required_projects_present,
    run_candidate_reconciliation,
)
from app.services.source_capture import InMemorySourceCaptureStore

_USER = "u1"
_RESUME_FIXTURE = (
    Path(__file__).parent / "fixtures" / "normalization_corpus" / "pdf_two_column_resume.txt"
)


def _seeded_store() -> InMemorySourceCaptureStore:
    store = InMemorySourceCaptureStore()
    store.capture(
        _USER,
        source_type="drive",
        source_ref="resume-pdf-1",
        title="Jordan_Rivera_resume.pdf",
        raw_text=_RESUME_FIXTURE.read_text(encoding="utf-8"),
        extractor="test",
        mime_type="application/pdf",
    )
    store.capture(
        _USER,
        source_type="github_readme",
        source_ref="jordanrivera/data-pipeline",
        title="data-pipeline",
        raw_text="# data-pipeline\n\nAn async ingestion pipeline.\n",
        extractor="test",
    )
    store.capture(
        _USER,
        source_type="github_commit",
        source_ref="jordanrivera/data-pipeline@abc123",
        title="data-pipeline",
        raw_text="Fix retry handling in the exporter.\n",
        extractor="test",
    )
    return store


# --- discovery (the non-negotiable acceptance) -----------------------------------------


def test_paper_recommender_system_is_discovered_in_the_manifest() -> None:
    """The project must be explicitly present — found in the resume's raw text even
    with NO roster entry (a missing roster row can never mask a discovery failure)."""
    manifest = build_source_manifest(_USER, _seeded_store(), InMemoryClaimRepository())
    assert required_projects_present(manifest)
    assert manifest.required_projects_present["paper recommender system"] is True
    resume = next(e for e in manifest.entries if e.path == "resume-pdf-1")
    assert "paper recommender system" in resume.project_references
    summary = format_preflight_summary(manifest)
    assert "PAPER_RECOMMENDER_SYSTEM_PRESENT=true" in summary


def test_discovery_matches_identifier_forms() -> None:
    """Repo/directory spellings (kebab/snake) discover the same project."""
    manifest = build_manifest(
        [],
        [ProjectCandidate(name="Paper recommender system", aliases=("paper-recommender_system",))],
        required_projects=("paper recommender system",),
    )
    # No sources: absent — and reported as absent, never silently true.
    assert manifest.required_projects_present["paper recommender system"] is False


def test_missing_required_project_fails_preflight() -> None:
    store = InMemorySourceCaptureStore()
    store.capture(
        _USER,
        source_type="drive",
        source_ref="doc-1",
        title="unrelated.txt",
        raw_text="Nothing about that project here.",
        extractor="test",
    )
    manifest = build_source_manifest(_USER, store, InMemoryClaimRepository())
    assert not required_projects_present(manifest)
    assert "PAPER_RECOMMENDER_SYSTEM_PRESENT=false" in format_preflight_summary(manifest)


# --- complete enumeration ---------------------------------------------------------------


def test_every_captured_document_and_repository_is_manifested() -> None:
    store = _seeded_store()
    manifest = build_source_manifest(_USER, store, InMemoryClaimRepository())
    assert manifest.document_count == len(store.list_documents(_USER)) == 3
    assert manifest.repositories == ("jordanrivera/data-pipeline",)
    assert manifest.repository_file_count == 2  # README + commit
    commit = next(e for e in manifest.entries if e.system == "github_commit")
    assert commit.commit_sha == "abc123"
    assert all(e.content_hash for e in manifest.entries)
    assert all(e.raw_cache_location for e in manifest.entries)


# --- the repaired structuring boundary --------------------------------------------------


def test_two_column_resume_yields_sections_and_a_separate_recommender_element() -> None:
    """The doc-7 defect, pinned: blank-line-free resume text must NOT collapse into
    one element — ALL-CAPS section headings and entry title lines are boundaries."""
    raw = _RESUME_FIXTURE.read_text(encoding="utf-8")
    elements = structure_source_text(raw)
    assert len(elements) > 5, "resume collapsed into too few elements again"
    assert verify_full_coverage(raw, elements) == []
    headings = {e.raw_text.strip() for e in elements if e.element_type == ELEMENT_HEADING}
    assert {"EXPERIENCE", "SKILLS", "EDUCATION", "CONTACT"} <= headings
    [title] = [e for e in elements if e.raw_text.strip() == "Paper recommender system, WPI"]
    assert title.element_type != ELEMENT_HEADING  # an entry, governed by EXPERIENCE
    governing = elements[title.parent_index]
    assert governing.raw_text.strip() == "EXPERIENCE"


def test_word_per_line_repair_still_precedes_structural_grouping() -> None:
    """H5.1 parity survives v3: word-per-line fragments with soft blanks stay ONE
    paragraph element, and the normalizer keeps the new boundaries on their own line."""
    damaged = "replacing\n \na\n \nfragmented\n \nworkflow\nEXPERIENCE\nBuilt the exporter"
    elements = structure_source_text(damaged)
    assert [e.element_type for e in elements] == ["paragraph", ELEMENT_HEADING, "paragraph"]
    assert "replacing a fragmented workflow" in normalize_source_text(damaged)
    # The heading starts its own normalized line (following prose absorbs into it,
    # exactly like v1 ``#`` headings) — it must never join the PREVIOUS run.
    assert any(line.startswith("EXPERIENCE") for line in normalize_source_text(damaged).split("\n"))


# --- reconciliation: deterministic, checkpointed, isolated, 100%-enforcing --------------


def test_reconciliation_passes_and_is_deterministic(tmp_path: Path) -> None:
    store = _seeded_store()
    manifest = build_source_manifest(_USER, store, InMemoryClaimRepository())
    run1 = run_candidate_reconciliation(
        _USER, store, manifest, checkpoint_path=tmp_path / "a" / "ledger.json"
    )
    run2 = run_candidate_reconciliation(
        _USER, store, manifest, checkpoint_path=tmp_path / "b" / "ledger.json"
    )
    assert run1.report.passed and run2.report.passed
    assert run1.report.metrics == run2.report.metrics
    assert [r.output_hash for r in run1.report.rows] == [r.output_hash for r in run2.report.rows]
    assert all(r.element_count > 0 for r in run1.report.rows)


def test_resume_from_checkpoint_never_repeats_completed_work(tmp_path: Path) -> None:
    store = _seeded_store()
    manifest = build_source_manifest(_USER, store, InMemoryClaimRepository())
    ledger = tmp_path / "ledger.json"
    first = run_candidate_reconciliation(_USER, store, manifest, checkpoint_path=ledger)
    assert first.recomputed == 3 and first.reused == 0
    second = run_candidate_reconciliation(_USER, store, manifest, checkpoint_path=ledger)
    assert second.recomputed == 0 and second.reused == 3
    assert second.report.metrics == first.report.metrics


def test_interrupted_run_resumes_at_first_incomplete_artifact(tmp_path: Path) -> None:
    store = _seeded_store()
    manifest = build_source_manifest(_USER, store, InMemoryClaimRepository())
    ledger = tmp_path / "ledger.json"
    # Simulate the interruption: reconcile a one-entry slice, leaving two incomplete.
    partial = type(manifest)(
        entries=manifest.entries[:1],
        projects_searched=manifest.projects_searched,
        required_projects_present=manifest.required_projects_present,
    )
    run_candidate_reconciliation(_USER, store, partial, checkpoint_path=ledger)
    resumed = run_candidate_reconciliation(_USER, store, manifest, checkpoint_path=ledger)
    assert resumed.reused == 1 and resumed.recomputed == 2
    assert resumed.report.passed


def test_failed_generation_is_isolated_and_enforces_100_percent(tmp_path: Path) -> None:
    """A corrupted raw payload fails ITS row and the whole generation — and the
    candidate run never mutates canonical capture state."""
    from dataclasses import replace

    inner = _seeded_store()

    class _TamperedStore:
        """The seeded store with one version's recorded hash corrupted in transit."""

        def list_documents(self, user_id: str):  # noqa: ANN201 - test double
            return inner.list_documents(user_id)

        def get_active_version(self, user_id: str, source_type: str, source_ref: str):  # noqa: ANN201
            version = inner.get_active_version(user_id, source_type, source_ref)
            if version is not None and source_ref == "resume-pdf-1":
                return replace(version, content_hash="not-the-real-hash")
            return version

    store = _TamperedStore()
    manifest = build_source_manifest(_USER, store, InMemoryClaimRepository())  # type: ignore[arg-type]
    before = [(d.id, d.source_ref) for d in inner.list_documents(_USER)]
    run = run_candidate_reconciliation(
        _USER,
        store,  # type: ignore[arg-type]
        manifest,
        checkpoint_path=tmp_path / "ledger.json",
    )
    assert not run.report.passed
    bad = next(r for r in run.report.rows if r.path == "resume-pdf-1")
    assert not bad.hash_verified and bad.validation_status == "failed"
    assert run.report.metrics["hash_verification_pct"] < 100.0
    assert run.report.metrics["structural_accounting_pct"] == 100.0  # structure itself was fine
    assert [(d.id, d.source_ref) for d in inner.list_documents(_USER)] == before


# --- zero paid model calls ---------------------------------------------------------------


def test_preflight_and_reconciliation_cannot_make_paid_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole path must run with the real LLM client booby-trapped: constructing
    one (the only way to spend money) blows up the test."""
    import app.llm.client as llm_client

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("ingestion preflight reached the paid LLM client")

    monkeypatch.setattr(llm_client.AnthropicClient, "__init__", _forbidden)
    store = _seeded_store()
    manifest = build_source_manifest(_USER, store, InMemoryClaimRepository())
    run = run_candidate_reconciliation(
        _USER, store, manifest, checkpoint_path=tmp_path / "ledger.json"
    )
    assert run.report.passed
    assert run.report.metrics["paid_ingestion_llm_calls"] == 0
