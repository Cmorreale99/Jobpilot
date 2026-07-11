"""V3 Phase 1: the cross-entity overlap pass (T10) and the unassigned-evidence queue.

The overlap pass is the deterministic replacement for Revision 1's impossible
synthesis-time duplicate detection: per-entity synthesis sees one entity per call, so
only a corpus-wide pass can catch the live failure — the same hackathon Result
("Top 3 out of 100+ teams", claims 472/513) sitting under two confirmed entities.
The unassigned queue is red-team case 3: an unproposed project's chunks must be a
review surface, never a silently dropped log line.
"""

from __future__ import annotations

import pytest
from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_README,
    Claim,
    ClaimEvidenceLink,
    ClaimEvidenceRef,
    ClaimField,
    ClaimStatus,
    DraftClaim,
    EvidenceChunk,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    ResultKind,
    ResultStatus,
    StorableClaim,
    StoredEvidence,
)
from app.domain.roster import MIN_SHARED_CHUNK_CHARS, detect_entity_overlaps
from app.domain.validation_runs import KIND_ENTITY_OVERLAP
from app.main import create_app
from app.services.claim_repository import InMemoryClaimRepository
from app.services.roster import run_overlap_detection
from app.services.validation_run_log import InMemoryValidationRunLog
from fastapi.testclient import TestClient

_QUOTE = "Top 3 out of 100+ teams"


def _claim(
    claim_id: int,
    experience_id: int,
    *,
    outcome_quote: str | None = _QUOTE,
    status: ClaimStatus = ClaimStatus.PENDING_REVIEW,
) -> Claim:
    links: tuple[ClaimEvidenceLink, ...] = (
        ClaimEvidenceLink(evidence_id=1, field=ClaimField.ACTION),
    )
    if outcome_quote is not None:
        links += (
            ClaimEvidenceLink(
                evidence_id=claim_id, field=ClaimField.RESULT, outcome_quote=outcome_quote
            ),
        )
    return Claim(
        id=claim_id,
        user_id="u1",
        experience_id=experience_id,
        status=status,
        action_text=f"Built the pipeline variant {claim_id} with Python",
        action_tools=("Python",),
        result_text=outcome_quote,
        result_kind=ResultKind.MISSING
        if outcome_quote is None
        else ResultKind.QUALITATIVE_EVIDENCED,
        result_status=ResultStatus.UNVERIFIED,
        evidence=links,
    )


def _stored(evidence_id: int, experience_id: int | None, text: str) -> StoredEvidence:
    return StoredEvidence(
        id=evidence_id,
        user_id="u1",
        source_type=SOURCE_DRIVE,
        source_ref=f"drv_{evidence_id}#chars=0-{len(text)}",
        chunk_text=text,
        experience_id=experience_id,
    )


# --- the pure pass (T10) -------------------------------------------------------------


def test_shared_outcome_quote_across_entities_is_a_merge_prompt() -> None:
    """The 472/513 regression shape: one Result quote under two entities."""
    (overlap,) = detect_entity_overlaps([_claim(472, 10), _claim(513, 20)], [])
    assert (overlap.experience_a_id, overlap.experience_b_id) == (10, 20)
    assert overlap.shared_outcome_quotes == (_QUOTE,)


def test_quote_matching_survives_whitespace_reflow() -> None:
    (overlap,) = detect_entity_overlaps(
        [_claim(1, 10, outcome_quote="Top 3  out of\n100+ teams"), _claim(2, 20)], []
    )
    assert overlap.shared_outcome_quotes == (_QUOTE,)


def test_same_quote_within_one_entity_never_pairs_with_itself() -> None:
    assert detect_entity_overlaps([_claim(1, 10), _claim(2, 10)], []) == []


def test_rejected_claims_do_not_signal() -> None:
    claims = [_claim(1, 10), _claim(2, 20, status=ClaimStatus.REJECTED)]
    assert detect_entity_overlaps(claims, []) == []


def test_duplicate_chunk_text_across_entities_signals() -> None:
    text = "Led the Wellington platform rebuild across ingestion, storage, and reporting."
    assert len(text) >= MIN_SHARED_CHUNK_CHARS
    (overlap,) = detect_entity_overlaps([], [_stored(1, 10, text), _stored(2, 20, text)])
    assert overlap.shared_chunk_texts == (text,)
    assert overlap.shared_outcome_quotes == ()


def test_short_repeated_chunks_never_signal() -> None:
    # Section headers and tool lists legitimately repeat across distinct projects.
    assert detect_entity_overlaps([], [_stored(1, 10, "Skills"), _stored(2, 20, "Skills")]) == []


def test_shared_vocabulary_is_not_overlap() -> None:
    """Red-team 7: two projects sharing tech + employer words must not false-fire."""
    a = "Built a Kafka ingestion service at Coopersmith for the carrier exception feeds."
    b = "Built a Kafka dashboard at Coopersmith for the finance reconciliation team."
    claims = [
        _claim(1, 10, outcome_quote="cut triage time in half"),
        _claim(2, 20, outcome_quote="doubled reporting throughput"),
    ]
    assert detect_entity_overlaps(claims, [_stored(1, 10, a), _stored(2, 20, b)]) == []


def test_unassigned_chunks_do_not_signal() -> None:
    text = "Led the Wellington platform rebuild across ingestion, storage, and reporting."
    assert detect_entity_overlaps([], [_stored(1, 10, text), _stored(2, None, text)]) == []


# --- the service wrapper ---------------------------------------------------------------


def _seed_pair(repo: InMemoryClaimRepository) -> tuple[int, int]:
    """Two CONFIRMED entities whose claims share one outcome quote."""
    ids = []
    for name, source_ref in (("OneWorld", "oneworld"), ("Portfolio", "portfolio")):
        experience = repo.propose_experience(
            "u1",
            ExperienceSeed(
                name=name,
                section=ExperienceSection.PROJECTS_HACKATHONS,
                kind=ExperienceKind.PROJECT,
            ),
        )
        repo.set_experience_status(experience.id, ExperienceStatus.CONFIRMED)
        chunk = EvidenceChunk(
            SOURCE_GITHUB_README,
            source_ref,
            f"The {name} hackathon build. {_QUOTE} at the finals across every division.",
        )
        stored = repo.upsert_evidence("u1", chunk)
        repo.assign_evidence(stored.id, experience.id)
        draft = DraftClaim(
            action_text=f"Built the {name} submission with Python",
            action_tools=("Python",),
            result_text=_QUOTE,
            result_kind=ResultKind.QUALITATIVE_EVIDENCED,
            result_metric_json={"resolves": None},
            evidence=(
                ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),
                ClaimEvidenceRef(chunk=chunk, field=ClaimField.RESULT, outcome_quote=_QUOTE),
            ),
        )
        repo.replace_unreviewed_claims(
            "u1",
            experience.id,
            [
                StorableClaim(
                    draft=draft,
                    status=ClaimStatus.PENDING_REVIEW,
                    result_status=ResultStatus.VERIFIED,
                )
            ],
        )
        ids.append(experience.id)
    return ids[0], ids[1]


def test_overlap_service_names_entities_and_records_the_run() -> None:
    repo = InMemoryClaimRepository()
    _seed_pair(repo)
    log = InMemoryValidationRunLog()
    report = run_overlap_detection("u1", repo, validation_log=log)

    (prompt,) = report.prompts
    assert {prompt.experience_a.name, prompt.experience_b.name} == {"OneWorld", "Portfolio"}
    assert prompt.shared_outcome_quotes == (_QUOTE,)
    (run,) = log.list_runs("u1", KIND_ENTITY_OVERLAP)
    assert not run.passed
    assert "OneWorld" in run.detail[0] and "Portfolio" in run.detail[0]


def test_clean_roster_records_a_passing_run() -> None:
    repo = InMemoryClaimRepository()
    log = InMemoryValidationRunLog()
    report = run_overlap_detection("u1", repo, validation_log=log)
    assert report.prompts == []
    (run,) = log.list_runs("u1", KIND_ENTITY_OVERLAP)
    assert run.passed


def test_merging_the_pair_resolves_the_prompt() -> None:
    """The prompt is a relation resolved by the existing roster merge — nothing else."""
    repo = InMemoryClaimRepository()
    source_id, target_id = _seed_pair(repo)
    repo.merge_experiences(source_id, target_id)
    assert run_overlap_detection("u1", repo).prompts == []


# --- the unassigned queue + routes ------------------------------------------------------


@pytest.fixture
def repo() -> InMemoryClaimRepository:
    return InMemoryClaimRepository()


@pytest.fixture
def client(repo: InMemoryClaimRepository) -> TestClient:
    return TestClient(create_app(claim_repository=repo))


def test_unassigned_queue_lists_only_unclaimed_chunks(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    experience = repo.propose_experience(
        "u1", ExperienceSeed(name="Wellington", section=ExperienceSection.PROFESSIONAL_EXPERIENCE)
    )
    repo.set_experience_status(experience.id, ExperienceStatus.CONFIRMED)
    claimed = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv_a", "assigned text"))
    repo.assign_evidence(claimed.id, experience.id)
    orphan = repo.upsert_evidence(
        "u1", EvidenceChunk(SOURCE_DRIVE, "drv_b", "a third project nobody proposed")
    )

    body = client.get("/roster/unassigned", params={"user_id": "u1"}).json()
    assert body["count"] == 1
    (item,) = body["items"]
    assert item["id"] == orphan.id
    assert item["chunk_text"] == "a third project nobody proposed"


def test_manual_assignment_resolves_the_queue_item(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    experience = repo.propose_experience(
        "u1", ExperienceSeed(name="Wellington", section=ExperienceSection.PROFESSIONAL_EXPERIENCE)
    )
    repo.set_experience_status(experience.id, ExperienceStatus.CONFIRMED)
    orphan = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv_b", "orphan chunk"))

    response = client.post(
        f"/roster/evidence/{orphan.id}/assign", json={"experience_id": experience.id}
    )
    assert response.status_code == 200
    assert response.json()["experience_id"] == experience.id
    # The API stamps the human label — this is what pins the row against re-runs (H1).
    assert response.json()["assignment_method"] == "human"
    assert client.get("/roster/unassigned", params={"user_id": "u1"}).json()["count"] == 0
    assert repo.list_assigned_evidence("u1", experience.id)


def test_assignment_to_unconfirmed_entity_is_409(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    proposed = repo.propose_experience(
        "u1", ExperienceSeed(name="Maybe", section=ExperienceSection.PROJECTS_HACKATHONS)
    )
    orphan = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv_b", "orphan chunk"))
    response = client.post(
        f"/roster/evidence/{orphan.id}/assign", json={"experience_id": proposed.id}
    )
    assert response.status_code == 409
    assert repo.get_evidence(orphan.id).experience_id is None  # type: ignore[union-attr]


def test_assignment_404s_for_unknown_evidence_or_entity(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    assert client.post("/roster/evidence/999/assign", json={"experience_id": 1}).status_code == 404
    orphan = repo.upsert_evidence("u1", EvidenceChunk(SOURCE_DRIVE, "drv_b", "orphan chunk"))
    response = client.post(f"/roster/evidence/{orphan.id}/assign", json={"experience_id": 999})
    assert response.status_code == 404


def test_overlaps_route_serves_merge_prompts(
    client: TestClient, repo: InMemoryClaimRepository
) -> None:
    _seed_pair(repo)
    (prompt,) = client.get("/roster/overlaps", params={"user_id": "u1"}).json()
    assert {prompt["entity_a"]["name"], prompt["entity_b"]["name"]} == {"OneWorld", "Portfolio"}
    assert prompt["shared_outcome_quotes"] == [_QUOTE]
