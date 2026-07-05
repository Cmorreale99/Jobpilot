"""Offline tests for the LLM roster proposer and chunk assigner (FakeLlmClient)."""

from __future__ import annotations

import json

import pytest
from app.domain.claims import (
    SOURCE_DRIVE,
    Experience,
    ExperienceKind,
    ExperienceSection,
)
from app.domain.roster import RosterDetectionError, SourceDocument
from app.llm.fake import FakeLlmClient
from app.llm.roster import LlmChunkAssigner, LlmRosterProposer

_DOCS = [
    SourceDocument(
        SOURCE_DRIVE,
        "drv_case_studies",
        "Data Systems Case Studies (2).pdf",
        "Wellington platform rebuild ... PFAS contamination reporting ...",
    ),
    SourceDocument(SOURCE_DRIVE, "drv_resume_v1", "resume_final (1).pdf", "Coopersmith Data ..."),
    SourceDocument(SOURCE_DRIVE, "drv_resume_v2", "resume_final_FINAL.pdf", "Coopersmith Data ..."),
]


def _entities_json() -> str:
    return json.dumps(
        {
            "entities": [
                {
                    "name": "Coopersmith Data — Data Engineer",
                    "kind": "employer_role",
                    "subtitle": None,
                    "dates": "Jun 2025–Present",
                    "aliases": ["Coopersmith"],
                    "source_refs": ["drv_resume_v1", "drv_resume_v2"],
                },
                {
                    "name": "Wellington platform rebuild",
                    "kind": "project",
                    "subtitle": None,
                    "dates": None,
                    "aliases": ["Wellington"],
                    "source_refs": ["drv_case_studies"],
                },
                {
                    "name": "PFAS contamination reporting",
                    "kind": "project",
                    "subtitle": None,
                    "dates": None,
                    "aliases": ["PFAS"],
                    "source_refs": ["drv_case_studies"],
                },
            ]
        }
    )


def test_multi_project_doc_yields_multiple_entities_and_resumes_dedupe() -> None:
    """Audit test case 5: the case-study doc is >= 2 projects, not one filename."""
    proposer = LlmRosterProposer(FakeLlmClient([_entities_json()]))
    entities = proposer.propose(_DOCS)

    assert len(entities) == 3  # two resume versions proposed ONE employer entity
    case_study_entities = [e for e in entities if "drv_case_studies" in e.source_refs]
    assert len(case_study_entities) == 2  # the multi-project doc split into projects
    employer = next(e for e in entities if e.kind is ExperienceKind.EMPLOYER_ROLE)
    assert employer.section is ExperienceSection.PROFESSIONAL_EXPERIENCE
    assert employer.dates == "Jun 2025–Present"
    assert set(employer.source_refs) == {"drv_resume_v1", "drv_resume_v2"}


def test_proposer_rejects_malformed_entities_via_json_retry() -> None:
    bad_then_good = [json.dumps({"entities": [{"kind": "project"}]}), _entities_json()]
    proposer = LlmRosterProposer(FakeLlmClient(bad_then_good))
    assert len(proposer.propose(_DOCS)) == 3  # the parse failure triggered one retry


def test_proposer_failure_is_loud() -> None:
    with pytest.raises(RosterDetectionError):
        LlmRosterProposer(FakeLlmClient(["nope", "still nope"])).propose(_DOCS)


def _roster() -> list[Experience]:
    return [
        Experience(
            id=11,
            user_id="u1",
            name="Wellington platform rebuild",
            section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        ),
        Experience(
            id=12,
            user_id="u1",
            name="PFAS contamination reporting",
            section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        ),
    ]


def test_assigner_maps_chunks_and_nulls_honestly() -> None:
    payload = json.dumps(
        {
            "assignments": [
                {"chunk_index": 0, "experience_id": 11},
                {"chunk_index": 1, "experience_id": 12},
                {"chunk_index": 2, "experience_id": None},
            ]
        }
    )
    assigner = LlmChunkAssigner(FakeLlmClient([payload]))
    assert assigner.assign(["a", "b", "c"], _roster()) == [11, 12, None]


def test_assigner_drops_hallucinated_ids_and_missing_indices() -> None:
    payload = json.dumps(
        {"assignments": [{"chunk_index": 0, "experience_id": 999}]}  # not in the roster
    )
    assigner = LlmChunkAssigner(FakeLlmClient([payload]))
    assert assigner.assign(["a", "b"], _roster()) == [None, None]


def test_assigner_failure_is_loud() -> None:
    with pytest.raises(RosterDetectionError):
        LlmChunkAssigner(FakeLlmClient(["nope", "still nope"])).assign(["a"], _roster())
