"""Evidence lifecycle: supersede, never orphan (hardening H6).

The acceptance criteria under test: after any re-ingest, the active rows for a base
ref are exactly the current chunking; a row the fresh chunk set no longer produces is
marked inactive — pointing at its overlapping successor when determinable — never
deleted, so ``claim_evidence`` links stay intact; a human pin carries forward to the
row that now holds the same text (H1 preserved); a REVIEWED claim citing superseded
evidence is surfaced loudly and never auto-resolved; an unchanged re-run supersedes
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.config import Settings
from app.db.base import Base
from app.db.claim_repository import SqlClaimRepository
from app.db.session import create_session_factory
from app.domain.claims import (
    ASSIGNMENT_HEURISTIC,
    ASSIGNMENT_HUMAN,
    SOURCE_DRIVE,
    ClaimEvidenceRef,
    ClaimField,
    ClaimRepository,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ResultStatus,
    StorableClaim,
    StoredEvidence,
)
from app.domain.evidence_lifecycle import plan_evidence_supersession
from app.domain.validation_runs import KIND_EVIDENCE_RECONCILIATION
from app.integrations.base import DriveDocument, DriveSource
from app.main import create_app
from app.services.claim_repository import InMemoryClaimRepository
from app.services.roster import (
    list_reviewed_claims_on_superseded_evidence,
    run_roster_assignment,
)
from app.services.validation_run_log import InMemoryValidationRunLog
from fastapi.testclient import TestClient

# --- fakes ------------------------------------------------------------------------------


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
    async def list_candidate_repos(self, user_id: str) -> list:
        return []

    async def read_repo(self, repo_ref: str):  # pragma: no cover - never called
        raise AssertionError

    async def list_commits(self, repo_ref: str) -> list:
        return []


def _settings(**overrides: object) -> Settings:
    return Settings(gdrive_source_folder_id="career_docs", **overrides)  # type: ignore[arg-type]


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


def _confirm(repo: ClaimRepository, name: str, *aliases: str) -> int:
    seed = ExperienceSeed(
        name=name,
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        kind=ExperienceKind.EMPLOYER_ROLE,
        aliases=tuple(aliases),
    )
    return repo.upsert_experience("u1", seed).id


_DOC_V1 = """# Coopersmith Data

Rebuilt the settlement exporter with Python after duplicate rows overstated charges.

Cut reconciliation time from four hours to twenty minutes.
"""

_DOC_V2 = """# Coopersmith Data

Rebuilt the settlement exporter with Python after duplicate rows overstated charges.

Documented the new exporter pipeline for the operations team.
"""


async def _assign(repo, doc_text: str, log=None, **overrides):
    return await run_roster_assignment(
        _drive(doc_text),
        FakeGitHubClient(),
        "u1",
        repo,
        _settings(**overrides),
        validation_log=log,
    )


# --- pure planner -------------------------------------------------------------------------


def _row(row_id: int, text: str, *, ref: str = "drv1#chars=0-10", **kw) -> StoredEvidence:
    return StoredEvidence(
        id=row_id,
        user_id="u1",
        source_type=SOURCE_DRIVE,
        source_ref=ref,
        chunk_text=text,
        **kw,
    )


def test_planner_supersedes_stale_rows_with_exact_text_successor() -> None:
    old = _row(1, "Cut reconciliation time from four hours to twenty minutes.")
    fresh = _row(
        9, "Cut reconciliation time from four hours to twenty minutes.", ref="drv1#chars=50-110"
    )
    plan = plan_evidence_supersession([old], [fresh])
    assert [(a.evidence_id, a.successor_id) for a in plan.supersede] == [(1, 9)]
    assert plan.new_ids == (9,)


def test_planner_matches_containment_successor_and_none_when_text_vanished() -> None:
    old_piece = _row(1, "duplicate rows overstated charges")
    vanished = _row(2, "This sentence no longer exists anywhere downstream.")
    fresh = _row(
        9,
        "Rebuilt the settlement exporter with Python after duplicate rows overstated charges.",
        ref="drv1#chars=0-90",
    )
    plan = plan_evidence_supersession([old_piece, vanished], [fresh])
    by_id = {a.evidence_id: a.successor_id for a in plan.supersede}
    assert by_id == {1: 9, 2: None}


def test_planner_migrates_human_pin_to_successor_and_warns_when_it_cannot() -> None:
    pinned = _row(
        1,
        "Cut reconciliation time from four hours to twenty minutes.",
        assignment_method=ASSIGNMENT_HUMAN,
        experience_id=7,
    )
    orphaned_pin = _row(
        2,
        "Totally vanished pinned text.",
        ref="drv1#chars=200-230",
        assignment_method=ASSIGNMENT_HUMAN,
        experience_id=7,
    )
    fresh = _row(
        9,
        "Cut reconciliation time from four hours to twenty minutes.",
        ref="drv1#chars=50-110",
        assignment_method=ASSIGNMENT_HEURISTIC,
        experience_id=3,
    )
    plan = plan_evidence_supersession([pinned, orphaned_pin], [fresh])
    ((successor_id, experience_id),) = [
        (m.successor_id, m.experience_id) for m in plan.pin_migrations
    ]
    assert (successor_id, experience_id) == (9, 7)
    assert any("no determinable successor" in w for w in plan.warnings)


def test_pin_never_migrates_onto_a_broader_successor() -> None:
    """The live 2026-07-11 case: a 9-char word-chunk pin migrated by containment onto
    the whole intro paragraph — a `human` stamp over content no human reviewed. A
    broader successor keeps the supersession pointer; the pin becomes a warning."""
    pinned = _row(
        1,
        "ingestion",
        assignment_method=ASSIGNMENT_HUMAN,
        experience_id=7,
    )
    broader = _row(
        9,
        "Reverse-engineers broken data systems: schema design, data ingestion & more.",
        ref="drv1#chars=0-80",
        assignment_method=ASSIGNMENT_HEURISTIC,
        experience_id=3,
    )
    plan = plan_evidence_supersession([pinned], [broader])
    # Lineage is still recorded — only the decision does not transfer.
    assert [(a.evidence_id, a.successor_id) for a in plan.supersede] == [(1, 9)]
    assert plan.pin_migrations == ()
    assert any("broader" in w and "re-pin" in w for w in plan.warnings)


def test_pin_migrates_onto_a_piece_of_the_decided_text() -> None:
    """The human decided the WHOLE stale text, so a successor holding a piece of it
    inherits the decision soundly (a re-split chunk keeps its pin)."""
    pinned = _row(
        1,
        "Cut reconciliation time from four hours to twenty minutes. Documented it too.",
        assignment_method=ASSIGNMENT_HUMAN,
        experience_id=7,
    )
    piece = _row(
        9,
        "Cut reconciliation time from four hours to twenty minutes.",
        ref="drv1#chars=50-110",
        assignment_method=ASSIGNMENT_HEURISTIC,
        experience_id=3,
    )
    plan = plan_evidence_supersession([pinned], [piece])
    ((successor_id, experience_id),) = [
        (m.successor_id, m.experience_id) for m in plan.pin_migrations
    ]
    assert (successor_id, experience_id) == (9, 7)
    assert plan.warnings == ()


def test_planner_never_overwrites_a_human_decided_successor() -> None:
    pinned = _row(
        1,
        "Same text either way.",
        assignment_method=ASSIGNMENT_HUMAN,
        experience_id=7,
    )
    fresh = _row(
        9,
        "Same text either way.",
        ref="drv1#chars=50-71",
        assignment_method=ASSIGNMENT_HUMAN,
        experience_id=3,
    )
    plan = plan_evidence_supersession([pinned], [fresh])
    assert plan.pin_migrations == ()


def test_planner_counts_reactivations_and_flags_version_bumps() -> None:
    returning = _row(1, "Back again.", is_active=False, superseded_by_id=5)
    stale_old_version = _row(
        2, "Written under an old normalizer.", ref="drv1#chars=30-60", normalization_version=1
    )
    plan = plan_evidence_supersession(
        [returning, stale_old_version],
        [_row(1, "Back again.")],
        current_normalization_version=4,
    )
    assert plan.reactivated_ids == (1,)
    assert any("normalizer" in w and "v1" in w and "v4" in w for w in plan.warnings)


def test_planner_unchanged_rerun_supersedes_nothing() -> None:
    rows = [_row(1, "Stable text."), _row(2, "Also stable.", ref="drv1#chars=20-40")]
    plan = plan_evidence_supersession(rows, rows)
    assert plan.supersede == () and plan.new_ids == () and plan.reactivated_ids == ()


# --- repository lifecycle, both implementations ---------------------------------------------


@pytest.fixture(params=["in_memory", "sql"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> ClaimRepository:
    if request.param == "in_memory":
        return InMemoryClaimRepository()
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}")
    Base.metadata.create_all(engine)
    return SqlClaimRepository(create_session_factory(engine))


def test_supersede_hides_from_reads_but_never_deletes(repo: ClaimRepository) -> None:
    entity = _confirm(repo, "Coopersmith Data")
    stored = repo.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_DRIVE, "drv1#chars=0-12", "Original text")
    )
    repo.assign_evidence(stored.id, entity, method=ASSIGNMENT_HEURISTIC)
    successor = repo.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_DRIVE, "drv1#chars=0-13", "Original text.")
    )

    superseded = repo.supersede_evidence(stored.id, successor.id)
    assert superseded.is_active is False
    assert superseded.superseded_by_id == successor.id

    # Hidden from the working sets…
    assert stored.id not in {e.id for e in repo.list_assigned_evidence("u1", entity)}
    assert stored.id not in {e.id for e in repo.list_unassigned_evidence("u1")}
    # …but never gone: direct fetch and the base-ref history still hold it.
    fetched = repo.get_evidence(stored.id)
    assert fetched is not None and fetched.chunk_text == "Original text"
    history = repo.list_evidence_for_base_ref("u1", SOURCE_DRIVE, "drv1")
    assert {e.id for e in history} == {stored.id, successor.id}


def test_upsert_reactivates_a_superseded_ref(repo: ClaimRepository) -> None:
    stored = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv1#chars=0-9", "Come back"))
    repo.supersede_evidence(stored.id, None)
    revived = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv1#chars=0-9", "Come back"))
    assert revived.id == stored.id
    assert revived.is_active is True
    assert revived.superseded_by_id is None


# --- service end-to-end -----------------------------------------------------------------


async def test_changed_content_supersedes_and_extraction_sees_only_fresh() -> None:
    repo = InMemoryClaimRepository()
    entity = _confirm(repo, "Coopersmith Data")

    await _assign(repo, _DOC_V1)
    v1_rows = repo.list_assigned_evidence("u1", entity)
    reconciliation_text = "Cut reconciliation time from four hours to twenty minutes."
    assert any(reconciliation_text in r.chunk_text for r in v1_rows)

    report = await _assign(repo, _DOC_V2)
    assert report.reconciliation.superseded >= 1

    # Extraction's view (active assigned rows) is exactly the fresh chunking.
    fresh_rows = repo.list_assigned_evidence("u1", entity)
    assert not any(reconciliation_text in r.chunk_text for r in fresh_rows)
    assert any("Documented the new exporter pipeline" in r.chunk_text for r in fresh_rows)

    # The stale row is visible history, not a deletion: inactive, still fetchable.
    history = repo.list_evidence_for_base_ref("u1", SOURCE_DRIVE, "drv1")
    stale = [r for r in history if reconciliation_text in r.chunk_text]
    assert stale and all(not r.is_active for r in stale)


async def test_unchanged_rerun_supersedes_nothing_and_content_return_reactivates() -> None:
    repo = InMemoryClaimRepository()
    _confirm(repo, "Coopersmith Data")

    first = await _assign(repo, _DOC_V1)
    assert first.reconciliation.superseded == 0
    assert first.reconciliation.new == first.chunks

    second = await _assign(repo, _DOC_V1)
    assert second.reconciliation.superseded == 0
    assert second.reconciliation.new == 0
    assert second.reconciliation.reactivated == 0

    await _assign(repo, _DOC_V2)
    returned = await _assign(repo, _DOC_V1)
    assert returned.reconciliation.reactivated >= 1
    history = repo.list_evidence_for_base_ref("u1", SOURCE_DRIVE, "drv1")
    active_texts = {r.chunk_text for r in history if r.is_active}
    assert any("Cut reconciliation time" in t for t in active_texts)
    assert not any("Documented the new exporter" in t for t in active_texts)


async def test_reviewed_claim_on_superseded_evidence_is_loud_never_autoresolved() -> None:
    repo = InMemoryClaimRepository()
    log = InMemoryValidationRunLog()
    entity = _confirm(repo, "Coopersmith Data")

    await _assign(repo, _DOC_V1)
    target = next(
        r
        for r in repo.list_assigned_evidence("u1", entity)
        if "Cut reconciliation time" in r.chunk_text
    )
    chunk = EvidenceChunk(target.source_type, target.source_ref, target.chunk_text)
    (claim,) = repo.replace_unreviewed_claims(
        "u1",
        entity,
        [
            StorableClaim(
                draft=DraftClaim(
                    action_text="Rebuilt the settlement exporter with Python",
                    action_tools=("Python",),
                    evidence=(ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
            )
        ],
    )
    repo.transition_claim(claim.id, ClaimStatus.APPROVED)

    report = await _assign(repo, _DOC_V2, log=log)
    assert report.reconciliation.reviewed_stale_claims == 1
    assert any("reviewed claim" in w for w in report.reconciliation.warnings)

    (run,) = log.list_runs("u1", KIND_EVIDENCE_RECONCILIATION)
    assert run.passed is False  # loud in validation_runs
    assert any("re-review or re-extract" in line for line in run.detail)

    # Never auto-resolved: the claim stands, its links resolve, and the standing
    # queue still lists it on the next look.
    still = repo.get_claim(claim.id)
    assert still is not None and still.status is ClaimStatus.APPROVED
    ((stale_claim, stale_rows),) = list_reviewed_claims_on_superseded_evidence("u1", repo)
    assert stale_claim.id == claim.id
    assert all(repo.get_evidence(r.id) is not None for r in stale_rows)


async def test_human_pin_migrates_across_a_ref_change() -> None:
    """The live migration path: a pin on a legacy row whose span ref the current
    chunking no longer produces carries forward to the row holding the same text."""
    repo = InMemoryClaimRepository()
    _confirm(repo, "Coopersmith Data")
    other = _confirm(repo, "Wanderwell Travel App")

    # A pre-H5-shaped row: same text, a span ref today's chunking never emits.
    victim = repo.upsert_evidence(
        "u1",
        EvidenceChunk(
            SOURCE_DRIVE,
            "drv1#chars=400-459",
            "Cut reconciliation time from four hours to twenty minutes.",
        ),
    )
    repo.assign_evidence(victim.id, other, method=ASSIGNMENT_HUMAN)

    report = await _assign(repo, _DOC_V1)  # fresh chunking: the old ref dangles
    assert report.reconciliation.pins_migrated == 1

    old_row = repo.get_evidence(victim.id)
    assert old_row is not None and not old_row.is_active
    assert old_row.superseded_by_id is not None
    successor = repo.get_evidence(old_row.superseded_by_id)
    assert successor is not None
    assert successor.experience_id == other  # the retained decision carried forward
    assert successor.assignment_method == ASSIGNMENT_HUMAN

    # And the migrated pin holds against yet another machine run (H1).
    again = await _assign(repo, _DOC_V1)
    assert again.reconciliation.pins_migrated == 0
    refreshed = repo.get_evidence(successor.id)
    assert refreshed is not None and refreshed.experience_id == other


async def test_superseded_reviewed_endpoint_lists_decisions_on_vanished_text() -> None:
    repo = InMemoryClaimRepository()
    client = TestClient(create_app(claim_repository=repo))
    entity = _confirm(repo, "Coopersmith Data")

    await _assign(repo, _DOC_V1)
    target = next(
        r
        for r in repo.list_assigned_evidence("u1", entity)
        if "Cut reconciliation time" in r.chunk_text
    )
    chunk = EvidenceChunk(target.source_type, target.source_ref, target.chunk_text)
    (claim,) = repo.replace_unreviewed_claims(
        "u1",
        entity,
        [
            StorableClaim(
                draft=DraftClaim(
                    action_text="Rebuilt the settlement exporter with Python",
                    action_tools=("Python",),
                    evidence=(ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),),
                ),
                status=ClaimStatus.PENDING_REVIEW,
                result_status=ResultStatus.UNVERIFIED,
            )
        ],
    )
    repo.transition_claim(claim.id, ClaimStatus.APPROVED)

    empty = client.get("/roster/superseded-reviewed", params={"user_id": "u1"}).json()
    assert empty["count"] == 0

    await _assign(repo, _DOC_V2)
    body = client.get("/roster/superseded-reviewed", params={"user_id": "u1"}).json()
    assert body["count"] == 1
    (item,) = body["items"]
    assert item["claim_id"] == claim.id
    assert item["status"] == "approved"
    assert item["superseded_evidence"]
    assert "Cut reconciliation time" in item["superseded_evidence"][0]["chunk_text"]


def test_verbatim_upsert_under_newer_normalizer_restamps_the_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normalizer bump must not strand unchanged rows on the old stamp: a row the
    current run re-produces VERBATIM was just vouched for by the current normalizer,
    so the upsert re-stamps it (observed live: 1,525 stranded rows, 2026-07-12)."""
    import app.services.claim_repository as repo_module

    repository = InMemoryClaimRepository()
    monkeypatch.setattr(repo_module, "NORMALIZATION_VERSION", 1)
    old = repository.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_DRIVE, "doc#chars=0-10", "Same text.")
    )
    assert old.normalization_version == 1

    monkeypatch.setattr(repo_module, "NORMALIZATION_VERSION", 2)
    restamped = repository.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_DRIVE, "doc#chars=0-10", "Same text.")
    )
    assert restamped.id == old.id
    assert restamped.chunk_text == "Same text."
    assert restamped.normalization_version == 2
