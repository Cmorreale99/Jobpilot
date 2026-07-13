"""Evidence preservation acceptance tests (MASTER CV REPAIR §4.12/4.13, §5.4, §8,
§16.5/16.9/16.10/16.14).

Source truth: evidence exists independently of claims and stories. Nothing relevant
disappears for lacking a complete PAR shape; fragments survive at the evidence layer;
a failed extraction invalidates nothing; the whole inventory is queryable with derived
(never guessed) categories; and one authoritative doc outranks any volume of commits.
"""

from __future__ import annotations

import pytest
from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    ClaimExtractionError,
    ClaimStatus,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
)
from app.domain.evidence_inventory import (
    CATEGORY_SUPPORTING_IMPLEMENTATION,
    CATEGORY_UNRESOLVED,
    TIER_COMMIT,
    TIER_PROJECT_DOC,
    evidence_categories,
    source_importance,
)
from app.domain.project_story import MAX_STORY_ACTIONS, select_story_content
from app.main import create_app
from app.services.claim_extraction import run_claim_extraction
from app.services.claim_repository import InMemoryClaimRepository
from app.services.validation_run_log import InMemoryValidationRunLog
from fastapi.testclient import TestClient

USER = "u1"


def _offline_settings():
    """Explicitly offline: constructed kwargs override any ambient env/flags, so these
    tests can never select an LLM-backed extractor (no paid calls, ever)."""
    from app.config import Settings

    return Settings(
        llm_enabled=False,
        claims_llm_extraction=False,
        roster_llm_detection=False,
        story_llm_synthesis=False,
        problem_space_llm_detection=False,
        anthropic_api_key="",
    )


def _entity(repo: InMemoryClaimRepository, name: str = "Atlas Pipeline") -> int:
    seed = ExperienceSeed(
        name=name,
        section=ExperienceSection.PROJECTS_HACKATHONS,
        kind=ExperienceKind.PROJECT,
    )
    return repo.upsert_experience(USER, seed).id


def _seed_evidence(
    repo: InMemoryClaimRepository, entity_id: int, texts: list[str], *, source=SOURCE_DRIVE
) -> list[int]:
    ids = []
    for i, text in enumerate(texts):
        stored = repo.upsert_evidence(USER, EvidenceChunk(source, f"doc-{entity_id}-{i}", text))
        repo.assign_evidence(stored.id, entity_id, method="heuristic")
        ids.append(stored.id)
    return ids


# --- 16.9: non-PAR evidence survives extraction, visibly --------------------------------


@pytest.mark.asyncio
async def test_non_par_evidence_survives_extraction_and_stays_queryable() -> None:
    """Scope/responsibility/technology evidence with no pain point: zero rows vanish."""
    repo = InMemoryClaimRepository()
    entity_id = _entity(repo)
    texts = [
        "Responsible for the nightly Atlas ingestion scope across five Snowflake tables",
        "Designed the Atlas architecture around Kafka and dbt with idempotent loaders",
        "Technologies: Python, Airflow, Snowflake, dbt",
        "Partial rollout reached the first two regions",
    ]
    before = _seed_evidence(repo, entity_id, texts)

    await run_claim_extraction(USER, repo, _offline_settings(), extractor=None)

    after = {row.id: row for row in repo.list_all_evidence(USER)}
    for evidence_id in before:
        assert evidence_id in after, "an evidence row disappeared during extraction"
        assert after[evidence_id].is_active, "extraction deactivated an evidence row"
    # Claims lacking a Problem queue as readiness data — never dropped, never flagged
    # for the absence itself (V3 absence split, §12.6).
    claims = repo.list_claims(USER)
    for claim in claims:
        assert claim.status is ClaimStatus.PENDING_REVIEW
        assert not any("problem_missing" in flag for flag in claim.validation_flags)


# --- 16.10: fragments survive at the evidence layer; drops are reconstructable ---------


@pytest.mark.asyncio
async def test_fragment_evidence_survives_even_when_draft_is_dropped() -> None:
    repo = InMemoryClaimRepository()
    log = InMemoryValidationRunLog()
    entity_id = _entity(repo, name="coverage-restore")
    fragment = "Restored date coverage."
    [fragment_id] = _seed_evidence(repo, entity_id, [fragment])

    report = await run_claim_extraction(USER, repo, _offline_settings(), validation_log=log)

    stored = repo.get_evidence(fragment_id)
    assert stored is not None and stored.is_active, (
        "the fragment's evidence row must survive whatever happens to claim drafts"
    )
    # No invented expansion: any claim text stays within the source words.
    for claim in report.claims:
        for word in claim.action_text.split():
            assert word.strip(".,") in fragment or word.lower() in fragment.lower()


# --- 16.14: failed extraction preserves everything and reruns resume narrowly ----------


class _FailFor:
    """Heuristic-like extractor that fails outright for one named group."""

    def __init__(self, fail_name: str, inner) -> None:
        self._fail_name = fail_name
        self._inner = inner

    def extract(self, group: EvidenceGroup, violations=()) -> list:
        if group.experience.name == self._fail_name:
            raise ClaimExtractionError("simulated LLM failure")
        return self._inner.extract(group, violations)


@pytest.mark.asyncio
async def test_failed_extraction_preserves_evidence_and_resumes_only_failed_group() -> None:
    from app.domain.claims import HeuristicTwoPassExtractor

    repo = InMemoryClaimRepository()
    good = _entity(repo, name="GoodProject")
    bad = _entity(repo, name="BadProject")
    _seed_evidence(
        repo,
        good,
        [
            "Problem: manual spreadsheet exports wasted six analyst hours every week\n"
            "Rebuilt the export pipeline with Python and Airflow"
        ],
    )
    bad_ids = _seed_evidence(
        repo,
        bad,
        [
            "Problem: slow overnight ingest blocked the morning reporting window\n"
            "Rewrote the ingest loader in Python with batched upserts"
        ],
    )

    inner = HeuristicTwoPassExtractor()
    first = await run_claim_extraction(
        USER, repo, _offline_settings(), extractor=_FailFor("BadProject", inner)
    )
    assert "BadProject" in first.failed_groups
    # Raw evidence survives the failure untouched.
    for evidence_id in bad_ids:
        row = repo.get_evidence(evidence_id)
        assert row is not None and row.is_active
    good_claims = [c for c in repo.list_claims(USER) if c.experience_id == good]
    assert good_claims, "the healthy group's claims must persist"

    # Rerun with a working extractor: only the failed group re-extracts.
    second = await run_claim_extraction(USER, repo, _offline_settings(), extractor=inner)
    assert "GoodProject" in second.skipped_unchanged, (
        "an unchanged, successful group must not repeat its (paid) extraction"
    )
    assert any(c.experience_id == bad for c in repo.list_claims(USER)), (
        "the failed group must extract on the rerun"
    )


# --- 16.5: one authoritative doc outranks a hundred commits -----------------------------


@pytest.mark.asyncio
async def test_authoritative_doc_outranks_commit_volume_in_story_selection() -> None:
    repo = InMemoryClaimRepository()
    entity_id = _entity(repo, name="Ledger")
    readme_text = (
        "Problem: settlement reconciliation cost the team 9 hours weekly by hand\n"
        "Rebuilt the Ledger reconciliation pipeline with Python and Kafka\n"
        "Result: cut reconciliation to 20 minutes"
    )
    stored = repo.upsert_evidence(
        USER, EvidenceChunk(SOURCE_GITHUB_README, "cm/ledger", readme_text)
    )
    repo.assign_evidence(stored.id, entity_id, method="readme_ref")
    for i in range(30):
        commit = repo.upsert_evidence(
            USER,
            EvidenceChunk(
                SOURCE_GITHUB_COMMIT, f"cm/ledger@c{i}", f"Refactored module {i} with Python"
            ),
        )
        repo.assign_evidence(commit.id, entity_id, method="repo_ref")

    await run_claim_extraction(USER, repo, _offline_settings())
    claims = [c for c in repo.list_claims(USER) if c.experience_id == entity_id]
    content = select_story_content(entity_id, claims)

    assert content.problem_text, "the story must carry the documented problem"
    assert "reconciliation" in (content.problem_text or ""), (
        "identity/narrative must come from the authoritative doc, not commit noise"
    )
    assert content.results, "the documented result must survive selection"
    assert len(content.actions) <= MAX_STORY_ACTIONS, "commit volume must not dominate"
    lead_action = content.actions[0]
    lead_claim = next(c for c in claims if c.id in lead_action.claim_ids)
    assert any(link.evidence_id == stored.id for link in lead_claim.evidence), (
        "the leading action must be the README-backed one, not a commit refactor"
    )


# --- inventory: derived categories + §6.3 importance ------------------------------------


def test_inventory_categories_and_importance_are_derived_not_guessed() -> None:
    repo = InMemoryClaimRepository()
    entity_id = _entity(repo, name="Inventory")
    assigned = repo.upsert_evidence(USER, EvidenceChunk(SOURCE_DRIVE, "d1", "Scope of the work"))
    repo.assign_evidence(assigned.id, entity_id, method="heuristic")
    commit = repo.upsert_evidence(
        USER, EvidenceChunk(SOURCE_GITHUB_COMMIT, "cm/x@1", "Fix retry logic")
    )
    commit = repo.assign_evidence(commit.id, entity_id, method="repo_ref")
    unassigned = repo.upsert_evidence(
        USER, EvidenceChunk(SOURCE_DRIVE, "d2", "Unclaimed but relevant text")
    )

    claims = repo.list_claims(USER)
    assert evidence_categories(commit, claims) == (CATEGORY_SUPPORTING_IMPLEMENTATION,)
    assert evidence_categories(unassigned, claims) == (CATEGORY_UNRESOLVED,)
    assert source_importance(SOURCE_GITHUB_COMMIT) == TIER_COMMIT
    assert source_importance(SOURCE_DRIVE) == TIER_PROJECT_DOC
    assert source_importance("github_doc", "cm/x/CLAUDE.md") == TIER_PROJECT_DOC


def test_evidence_inventory_endpoint_serves_every_row() -> None:
    repo = InMemoryClaimRepository()
    entity_id = _entity(repo, name="ApiInventory")
    assigned = repo.upsert_evidence(USER, EvidenceChunk(SOURCE_DRIVE, "d1", "Assigned text"))
    repo.assign_evidence(assigned.id, entity_id, method="heuristic")
    repo.upsert_evidence(USER, EvidenceChunk(SOURCE_DRIVE, "d2", "Unassigned text"))

    client = TestClient(create_app(claim_repository=repo))
    payload = client.get("/roster/evidence", params={"user_id": USER}).json()

    assert payload["count"] == 2
    by_ref = {item["source_ref"]: item for item in payload["items"]}
    assert by_ref["d1"]["experience_id"] == entity_id
    assert by_ref["d2"]["categories"] == ["unresolved"]
    assert all("importance_tier" in item and "cited_by" in item for item in payload["items"])
