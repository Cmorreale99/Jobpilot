"""Structure-aware chunking + section-scoped ownership (hardening H5) — the Cooper fix.

The acceptance criteria under test: a chunk under a heading naming entity X can never
be silently assigned to entity Y (a Result paragraph with zero entity tokens inherits
its section's owner); chunks are cut from elements and never cross an element
boundary; document order is reconstructable from columns; the human section pin is a
retained decision (H1); the flat-text path survives behind the rollback flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from app.config import Settings
from app.domain.chunking import chunk_elements
from app.domain.claims import (
    ASSIGNMENT_HUMAN,
    ASSIGNMENT_README_REF,
    ASSIGNMENT_REPO_REF,
    ASSIGNMENT_SECTION,
    SOURCE_DRIVE,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    split_span_ref,
)
from app.domain.roster import HeuristicSectionAssigner, SectionContent
from app.domain.source_structure import (
    heading_trail,
    structure_source_text,
    top_level_sections,
)
from app.integrations.base import (
    DriveDocument,
    DriveSource,
    GitHubCommit,
    GitHubDocument,
    GitHubRepo,
)
from app.main import create_app
from app.services.claim_repository import InMemoryClaimRepository
from app.services.roster import run_roster_assignment
from app.services.source_capture import InMemorySourceCaptureStore
from fastapi.testclient import TestClient

# --- fixtures -------------------------------------------------------------------------

# The Cooper regression document (PIPELINE_HARDENING_PLAN.md F3): the Result paragraph
# under the Cooper section contains ZERO entity tokens — per-chunk lexical assignment
# guessed it onto whatever scored nonzero (or dropped it); section ownership inherits.
_COOPER_DOC = """# Cooper.ai — Data Engineering

## FedEx migration

Migrated the carrier ETL to the new FedEx endpoints using Python and Airflow.

Cut nightly load failures from fourteen percent to zero across the quarter.

Reused the Wanderwell Travel App recommender code for internal tooling.

# Wanderwell Travel App

Built an itinerary recommender with collaborative filtering and Postgres.
"""

_RESULT_TEXT = "Cut nightly load failures from fourteen percent to zero across the quarter."
_TIE_BAIT_TEXT = "Reused the Wanderwell Travel App recommender code for internal tooling."


@dataclass
class FakeDriveClient:
    sources: list[DriveSource] = field(default_factory=list)
    documents: dict[str, DriveDocument] = field(default_factory=dict)

    async def list_candidate_sources(self, user_id: str) -> list[DriveSource]:
        return list(self.sources)

    async def read_source(self, source_ref: str) -> DriveDocument:
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


def _settings(**overrides: object) -> Settings:
    return Settings(
        gdrive_source_folder_id="career_docs",
        github_username="jordanrivera",
        **overrides,  # type: ignore[arg-type]
    )


def _drive(doc_text: str, ref: str = "drv1") -> FakeDriveClient:
    return FakeDriveClient(
        sources=[
            DriveSource(
                source_ref=ref, title="Doc", mime_type="text/plain", folder_id="career_docs"
            )
        ],
        documents={
            ref: DriveDocument(source_ref=ref, title="Doc", mime_type="text/plain", text=doc_text)
        },
    )


def _confirm(repo: InMemoryClaimRepository, name: str, *aliases: str) -> int:
    seed = ExperienceSeed(
        name=name,
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        kind=ExperienceKind.EMPLOYER_ROLE,
        aliases=tuple(aliases),
    )
    return repo.upsert_experience("u1", seed).id


# --- pure chunking: never across an element boundary -----------------------------------


def test_chunk_elements_slices_raw_verbatim_one_chunk_per_element() -> None:
    elements = structure_source_text(_COOPER_DOC)
    chunks = chunk_elements(elements)
    assert len(chunks) == len(elements)  # everything fits: one chunk per element
    for chunk in chunks:
        assert _COOPER_DOC[chunk.raw_start : chunk.raw_end] == chunk.text
        owner = elements[chunk.element_index]
        assert owner.raw_start <= chunk.raw_start <= chunk.raw_end <= owner.raw_end


def test_oversized_element_splits_at_sentence_boundaries_within_itself() -> None:
    sentences = " ".join(
        f"Sentence number {i} describes one more processing step." for i in range(40)
    )
    raw = f"# Report\n\n{sentences}\n"
    elements = structure_source_text(raw)
    chunks = chunk_elements(elements)
    paragraph = next(e for e in elements if e.element_type == "paragraph")
    pieces = [c for c in chunks if c.element_index == paragraph.sequence_index]
    assert len(pieces) > 1
    for piece in pieces:
        assert len(piece.text) <= 1200
        # Verbatim slice, contained in its OWN element — never across a boundary.
        assert raw[piece.raw_start : piece.raw_end] == piece.text
        assert paragraph.raw_start <= piece.raw_start <= piece.raw_end <= paragraph.raw_end
    for earlier, later in zip(pieces, pieces[1:], strict=False):
        assert earlier.raw_end <= later.raw_start
        assert earlier.text.rstrip().endswith(".")  # sentence-boundary cut


def test_whitespace_only_elements_yield_no_chunks() -> None:
    from app.domain.source_structure import SourceElement

    blank = SourceElement(0, "paragraph", 0, 3, "  \n")
    assert chunk_elements([blank]) == []


# --- section views ---------------------------------------------------------------------


def test_top_level_sections_partition_every_element_once() -> None:
    elements = structure_source_text(_COOPER_DOC)
    sections = top_level_sections(elements)
    assert [s.path for s in sections] == ["Cooper.ai — Data Engineering", "Wanderwell Travel App"]
    seen = [i for s in sections for i in s.element_indices]
    assert sorted(seen) == [e.sequence_index for e in elements]  # exactly once each


def test_heading_trail_walks_to_the_root() -> None:
    elements = structure_source_text(_COOPER_DOC)
    result = next(e for e in elements if _RESULT_TEXT in e.raw_text)
    assert heading_trail(elements, result.sequence_index) == (
        "Cooper.ai — Data Engineering > FedEx migration"
    )


def test_preamble_before_any_heading_is_its_own_section() -> None:
    raw = "Contact: jordan@example.com\n\n# Projects\n\nBuilt a thing.\n"
    elements = structure_source_text(raw)
    sections = top_level_sections(elements)
    assert sections[0].heading_index is None and sections[0].path is None
    assert sections[1].path == "Projects"


# --- heuristic section assigner ---------------------------------------------------------


def test_heading_decides_before_body_vocabulary() -> None:
    repo = InMemoryClaimRepository()
    cooper = _confirm(repo, "Cooper.ai")
    wanderwell = _confirm(repo, "Wanderwell Travel App")
    roster = repo.list_experiences("u1")
    assigner = HeuristicSectionAssigner()
    # The body names Wanderwell twice — the heading still wins.
    sections = [
        SectionContent(heading="Cooper.ai — Data Engineering", body=_TIE_BAIT_TEXT * 2),
        SectionContent(heading="Wanderwell Travel App", body="Built a recommender."),
    ]
    assert assigner.assign_sections(sections, roster) == [cooper, wanderwell]


def test_headingless_section_falls_back_to_body_and_refuses_ties() -> None:
    repo = InMemoryClaimRepository()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")
    roster = repo.list_experiences("u1")
    assigner = HeuristicSectionAssigner()
    assert assigner.assign_sections(
        [SectionContent(heading=None, body="Cooper.ai pipeline work.")], roster
    ) == [cooper]
    # Both entities score one token each in the body -> refused, honestly unowned.
    assert assigner.assign_sections(
        [SectionContent(heading=None, body="Cooper.ai work alongside the Wanderwell system.")],
        roster,
    ) == [None]


# --- the Cooper regression (service level) ----------------------------------------------


async def _assign_cooper_doc(
    repo: InMemoryClaimRepository,
    store: InMemorySourceCaptureStore | None,
    settings: Settings | None = None,
):
    return await run_roster_assignment(
        _drive(_COOPER_DOC),
        FakeGitHubClient(),
        "u1",
        repo,
        settings or _settings(),
        capture_store=store,
    )


async def test_cooper_regression_tokenless_result_inherits_its_section() -> None:
    """THE regression: a Result paragraph with zero entity tokens must inherit the
    section's entity — never land unassigned, never on a lexically-similar wrong one."""
    repo = InMemoryClaimRepository()
    store = InMemorySourceCaptureStore()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")

    report = await _assign_cooper_doc(repo, store)
    assert report.chunks > 0

    rows = repo.list_assigned_evidence("u1", cooper)
    result_row = next(r for r in rows if _RESULT_TEXT in r.chunk_text)
    assert result_row.experience_id == cooper
    assert result_row.assignment_method == ASSIGNMENT_SECTION
    assert result_row.section_path == "Cooper.ai — Data Engineering > FedEx migration"
    assert result_row.element_id is not None  # linked to the persisted tree
    # Nothing from the Cooper section leaked into Wanderwell or the unassigned queue.
    assert not any(_RESULT_TEXT in r.chunk_text for r in repo.list_unassigned_evidence("u1"))


async def test_heading_context_beats_vocabulary_inside_a_section() -> None:
    """A Cooper-section paragraph that NAMES Wanderwell still belongs to Cooper —
    ownership is the section's, not the paragraph vocabulary's."""
    repo = InMemoryClaimRepository()
    cooper = _confirm(repo, "Cooper.ai")
    wanderwell = _confirm(repo, "Wanderwell Travel App")

    await _assign_cooper_doc(repo, InMemorySourceCaptureStore())

    cooper_rows = repo.list_assigned_evidence("u1", cooper)
    assert any(_TIE_BAIT_TEXT in r.chunk_text for r in cooper_rows)
    assert not any(
        _TIE_BAIT_TEXT in r.chunk_text for r in repo.list_assigned_evidence("u1", wanderwell)
    )


async def test_problem_and_result_in_one_section_share_one_owner() -> None:
    repo = InMemoryClaimRepository()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")
    await _assign_cooper_doc(repo, InMemorySourceCaptureStore())
    owners = {
        r.experience_id
        for r in repo.list_assigned_evidence("u1", cooper)
        if r.section_path and r.section_path.startswith("Cooper.ai")
    }
    assert owners == {cooper}


async def test_oversized_section_keeps_uniform_ownership_across_split_chunks() -> None:
    sentences = " ".join(
        f"Step {i} of the migration reprocessed one more archived batch." for i in range(40)
    )
    doc = f"# Cooper.ai — Data Engineering\n\n{sentences}\n"
    repo = InMemoryClaimRepository()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")

    await run_roster_assignment(
        _drive(doc), FakeGitHubClient(), "u1", repo, _settings(), capture_store=None
    )
    rows = [r for r in repo.list_assigned_evidence("u1", cooper) if "Step" in r.chunk_text]
    assert len(rows) > 1  # the paragraph split
    assert {r.assignment_method for r in rows} == {ASSIGNMENT_SECTION}
    assert {r.experience_id for r in rows} == {cooper}


async def test_structureless_source_still_refuses_per_chunk_ties() -> None:
    doc = "Cooper.ai work alongside the Wanderwell system.\n"
    repo = InMemoryClaimRepository()
    _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")

    report = await run_roster_assignment(_drive(doc), FakeGitHubClient(), "u1", repo, _settings())
    assert report.chunks == 1 and report.assigned == 0  # tie refused, honestly unassigned
    (row,) = repo.list_unassigned_evidence("u1")
    assert row.section_path is None
    assert row.sequence_index is not None  # still element-linked for ordering


async def test_readme_tree_is_repo_owned_by_construction() -> None:
    readme = "# carrier-etl\n\n## Design\n\nAirflow DAGs feed the exporter.\n"
    github = FakeGitHubClient(
        repos=[
            GitHubRepo(
                repo_ref="jordanrivera/carrier-etl", name="carrier-etl", owner="jordanrivera"
            )
        ],
        readmes={
            "jordanrivera/carrier-etl": GitHubDocument(
                repo_ref="jordanrivera/carrier-etl", title="carrier-etl", text=readme
            )
        },
        commits={
            "jordanrivera/carrier-etl": [
                GitHubCommit(
                    repo_ref="jordanrivera/carrier-etl",
                    sha="abc123",
                    message="Fix exporter retries",
                )
            ]
        },
    )
    repo = InMemoryClaimRepository()
    project = _confirm(repo, "carrier-etl", "jordanrivera/carrier-etl")

    await run_roster_assignment(FakeDriveClient(), github, "u1", repo, _settings())
    rows = repo.list_assigned_evidence("u1", project)
    readme_rows = [r for r in rows if r.source_type == "github_readme"]
    assert readme_rows and {r.assignment_method for r in readme_rows} == {ASSIGNMENT_README_REF}
    commit_rows = [r for r in rows if r.source_type == "github_commit"]
    assert commit_rows and commit_rows[0].assignment_method == ASSIGNMENT_REPO_REF


async def test_assigned_evidence_returns_in_document_order_from_columns() -> None:
    repo = InMemoryClaimRepository()
    store = InMemorySourceCaptureStore()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")
    await _assign_cooper_doc(repo, store)

    rows = repo.list_assigned_evidence("u1", cooper)
    sequences = [r.sequence_index for r in rows]
    assert all(s is not None for s in sequences)
    assert sequences == sorted(sequences)  # document order from columns, no ref parsing
    # And the column order agrees with the raw spans the refs carry:
    starts = [split_span_ref(r.source_ref)[1] for r in rows]
    assert starts == sorted(starts)  # type: ignore[type-var]


async def test_human_pin_survives_structured_rerun() -> None:
    repo = InMemoryClaimRepository()
    cooper = _confirm(repo, "Cooper.ai")
    wanderwell = _confirm(repo, "Wanderwell Travel App")
    await _assign_cooper_doc(repo, InMemorySourceCaptureStore())

    victim = next(
        r for r in repo.list_assigned_evidence("u1", cooper) if _RESULT_TEXT in r.chunk_text
    )
    repo.assign_evidence(victim.id, wanderwell, method=ASSIGNMENT_HUMAN)

    report = await _assign_cooper_doc(repo, InMemorySourceCaptureStore())
    assert report.pinned >= 1
    refreshed = repo.get_evidence(victim.id)
    assert refreshed is not None
    assert refreshed.experience_id == wanderwell  # the human decision held
    assert refreshed.assignment_method == ASSIGNMENT_HUMAN


async def test_rollback_flag_restores_flat_text_assignment() -> None:
    repo = InMemoryClaimRepository()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")

    await run_roster_assignment(
        _drive(_COOPER_DOC),
        FakeGitHubClient(),
        "u1",
        repo,
        _settings(structured_assignment=False),
    )
    rows = repo.list_assigned_evidence("u1", cooper)
    assert rows  # the flat path still assigns what it can
    assert all(r.element_id is None and r.section_path is None for r in rows)
    assert ASSIGNMENT_SECTION not in {r.assignment_method for r in rows}
    # The token-less Result paragraph is back to unowned — exactly the pre-H5 behavior
    # the flag preserves for one release.
    assert any(_RESULT_TEXT in r.chunk_text for r in repo.list_unassigned_evidence("u1"))


async def test_rerun_is_idempotent_same_refs_same_owners() -> None:
    repo = InMemoryClaimRepository()
    store = InMemorySourceCaptureStore()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")
    first = await _assign_cooper_doc(repo, store)
    before = {(r.source_ref, r.experience_id) for r in repo.list_assigned_evidence("u1", cooper)}
    second = await _assign_cooper_doc(repo, store)
    after = {(r.source_ref, r.experience_id) for r in repo.list_assigned_evidence("u1", cooper)}
    assert first.chunks == second.chunks
    assert before == after


async def test_section_decisions_and_truncations_land_in_the_report() -> None:
    class StubSectionAssigner:
        method = "llm"
        last_truncated = 3

        def assign_sections(self, sections, roster):  # type: ignore[no-untyped-def]
            return [roster[0].id] * len(sections)

    repo = InMemoryClaimRepository()
    _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")
    report = await run_roster_assignment(
        _drive(_COOPER_DOC),
        FakeGitHubClient(),
        "u1",
        repo,
        _settings(),
        section_assigner=StubSectionAssigner(),
    )
    assert report.truncated_prompts == 3
    assert [d.path for d in report.sections] == [
        "Cooper.ai — Data Engineering",
        "Wanderwell Travel App",
    ]
    assert {d.method for d in report.sections} == {"llm"}


# --- span refs point into stored raw ----------------------------------------------------


async def test_structured_span_refs_are_raw_coordinates() -> None:
    repo = InMemoryClaimRepository()
    store = InMemorySourceCaptureStore()
    cooper = _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")
    await _assign_cooper_doc(repo, store)

    for row in repo.list_assigned_evidence("u1", cooper):
        base_ref, span = split_span_ref(row.source_ref)
        assert span is not None
        active = store.get_active_version("u1", SOURCE_DRIVE, base_ref)
        assert active is not None
        start, end = span
        from app.domain.text_normalization import normalize_source_text

        assert normalize_source_text(active.raw_text[start:end]) == row.chunk_text


# --- the section pin endpoint (H5 human correction at decision level) -------------------


@pytest.fixture
def pinned_world() -> tuple[TestClient, InMemoryClaimRepository, InMemorySourceCaptureStore]:
    repo = InMemoryClaimRepository()
    store = InMemorySourceCaptureStore()
    client = TestClient(create_app(claim_repository=repo, source_capture_store=store))
    return client, repo, store


async def test_section_pin_stamps_the_whole_subtree_human(
    pinned_world: tuple[TestClient, InMemoryClaimRepository, InMemorySourceCaptureStore],
) -> None:
    client, repo, store = pinned_world
    cooper = _confirm(repo, "Cooper.ai")
    wanderwell = _confirm(repo, "Wanderwell Travel App")
    await _assign_cooper_doc(repo, store)

    # The Cooper top-level heading element in the persisted tree:
    version = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert version is not None
    elements = store.list_elements(version.id)
    root = next(e for e in elements if e.sequence_index == 0)

    response = client.post(f"/roster/sections/{root.id}/assign", json={"experience_id": wanderwell})
    assert response.status_code == 200
    body = response.json()
    assert body["pinned"] > 1  # the whole subtree, not one chunk

    moved = repo.list_assigned_evidence("u1", wanderwell)
    assert any(_RESULT_TEXT in r.chunk_text for r in moved)
    assert all(
        r.assignment_method == ASSIGNMENT_HUMAN
        for r in moved
        if r.section_path and r.section_path.startswith("Cooper.ai")
    )

    # And the pin holds against a machine re-run (H1).
    await _assign_cooper_doc(repo, store)
    still = repo.list_assigned_evidence("u1", wanderwell)
    assert any(_RESULT_TEXT in r.chunk_text for r in still)
    assert not any(_RESULT_TEXT in r.chunk_text for r in repo.list_assigned_evidence("u1", cooper))


async def test_section_pin_guards_unknown_element_and_unconfirmed_entity(
    pinned_world: tuple[TestClient, InMemoryClaimRepository, InMemorySourceCaptureStore],
) -> None:
    client, repo, store = pinned_world
    _confirm(repo, "Cooper.ai")
    _confirm(repo, "Wanderwell Travel App")
    await _assign_cooper_doc(repo, store)

    assert client.post("/roster/sections/9999/assign", json={"experience_id": 1}).status_code == 404

    version = store.get_active_version("u1", SOURCE_DRIVE, "drv1")
    assert version is not None
    root = store.list_elements(version.id)[0]
    proposed = repo.propose_experience(
        "u1",
        ExperienceSeed(
            name="Unconfirmed Thing",
            section=ExperienceSection.PROJECTS_HACKATHONS,
            kind=ExperienceKind.PROJECT,
        ),
    )
    response = client.post(
        f"/roster/sections/{root.id}/assign", json={"experience_id": proposed.id}
    )
    assert response.status_code == 409
