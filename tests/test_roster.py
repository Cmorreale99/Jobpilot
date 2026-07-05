"""Roster domain: proposals, alias matching, heuristic proposer/assigner, span refs."""

from __future__ import annotations

from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    Experience,
    ExperienceKind,
    ExperienceSection,
    evidence_source_url,
    span_ref,
    split_span_ref,
)
from app.domain.roster import (
    HeuristicChunkAssigner,
    HeuristicRosterProposer,
    SourceDocument,
)


def _entity(id: int, name: str, aliases: tuple[str, ...] = ()) -> Experience:
    return Experience(
        id=id,
        user_id="u1",
        name=name,
        section=ExperienceSection.PROFESSIONAL_EXPERIENCE,
        aliases=aliases,
    )


# --- span refs ---------------------------------------------------------------------------


def test_span_ref_round_trips() -> None:
    ref = span_ref("drv_doc_1", 1204, 1688)
    assert ref == "drv_doc_1#chars=1204-1688"
    assert split_span_ref(ref) == ("drv_doc_1", (1204, 1688))
    assert split_span_ref("drv_doc_1") == ("drv_doc_1", None)


def test_evidence_source_url_strips_span_suffix() -> None:
    assert evidence_source_url(SOURCE_DRIVE, span_ref("drv1", 0, 100)) == (
        "https://drive.google.com/open?id=drv1"
    )
    assert evidence_source_url(SOURCE_GITHUB_README, span_ref("o/r", 5, 50)) == (
        "https://github.com/o/r"
    )


def test_span_ref_does_not_collide_with_pr_refs() -> None:
    # PR refs are "owner/repo#42" — the span suffix is distinctive.
    assert split_span_ref("owner/repo#42") == ("owner/repo#42", None)


# --- alias matching ----------------------------------------------------------------------


def test_matches_name_is_case_and_whitespace_insensitive_across_aliases() -> None:
    entity = _entity(1, "Wellington Management — Technology Intern", aliases=("Wellington",))
    assert entity.matches_name("wellington")
    assert entity.matches_name("  Wellington   Management — Technology Intern ")
    assert not entity.matches_name("Cooper.ai")


# --- heuristic proposer ------------------------------------------------------------------


def test_heuristic_proposes_one_entity_per_source_with_repo_aliases() -> None:
    documents = [
        SourceDocument(SOURCE_DRIVE, "drv1", "resume_final (1).pdf", "text"),
        SourceDocument(SOURCE_GITHUB_README, "o/carrier-etl", "carrier-etl", "readme"),
        SourceDocument(SOURCE_GITHUB_COMMIT, "o/carrier-etl@abc", "carrier-etl", "msg"),
    ]
    proposals = HeuristicRosterProposer().propose(documents)
    by_name = {p.name: p for p in proposals}
    # Commits propose nothing; the repo README and the Drive doc each propose once.
    assert set(by_name) == {"resume_final (1).pdf", "carrier-etl"}
    assert by_name["carrier-etl"].kind is ExperienceKind.PROJECT
    assert "o/carrier-etl" in by_name["carrier-etl"].aliases
    assert by_name["resume_final (1).pdf"].kind is ExperienceKind.EMPLOYER_ROLE


# --- heuristic assigner ------------------------------------------------------------------


def test_assigner_scores_alias_overlap_and_refuses_ties() -> None:
    wellington = _entity(1, "Wellington platform rebuild")
    pfas = _entity(2, "PFAS contamination reporting")
    assigner = HeuristicChunkAssigner()
    chunks = [
        "Rebuilt the Wellington platform ingestion layer with Airflow.",
        "Manual PFAS reporting consumed forty analyst hours monthly.",
        "Generic text mentioning neither entity at all.",
        "The Wellington contamination review.",  # one token each: a genuine tie
    ]
    result = assigner.assign(chunks, [wellington, pfas])
    assert result[0] == 1
    assert result[1] == 2
    assert result[2] is None
    assert result[3] is None  # ambiguous chunks are refused, never guessed
