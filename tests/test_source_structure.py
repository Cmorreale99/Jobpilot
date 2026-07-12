"""Canonical source structure (hardening H4): golden trees, coverage, versioning.

The invariants under test: the structurer is deterministic and idempotent, every
element's raw span slices the raw text verbatim, the heading hierarchy is carried as
parent links (the Cooper-shaped ownership substrate H5 consumes), and the coverage
reconciliation leaves zero characters silently unaccounted. The digest gate gives
``STRUCTURER_VERSION`` the same enforcement ``NORMALIZATION_VERSION`` got in H3.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.source_structure import (
    ELEMENT_BLOCKQUOTE,
    ELEMENT_CODE_BLOCK,
    ELEMENT_COMMIT_MESSAGE,
    ELEMENT_HEADING,
    ELEMENT_LIST_ITEM,
    ELEMENT_PARAGRAPH,
    STRUCTURER_VERSION,
    SourceElement,
    structure_commit_message,
    structure_source_text,
    verify_full_coverage,
)
from app.tools.structure_digest import (
    CORPUS_DIR,
    DIGEST_FILE,
    compute_structure_digest,
    read_blessed,
)

_MARKDOWN = (
    "# Cooper.ai\n"
    "\n"
    "Duplicate FedEx rows overstated charges.\n"
    "\n"
    "## FedEx migration\n"
    "\n"
    "- Removed 195K+ duplicate\n"
    "  records\n"
    "- Corrected reporting\n"
    "\n"
    "```sql\n"
    "SELECT 1;\n"
    "```\n"
    "\n"
    "> a quote\n"
    "> continues\n"
    "\n"
    "## Pacifica\n"
    "\n"
    "Automated the reporting flow.\n"
)


def test_markdown_golden_tree() -> None:
    """The Cooper shape: sections own their content via parent links, in order."""
    elements = structure_source_text(_MARKDOWN)
    shape = [(e.element_type, e.level, e.parent_index) for e in elements]
    assert shape == [
        (ELEMENT_HEADING, 1, None),  # 0: # Cooper.ai
        (ELEMENT_PARAGRAPH, None, 0),  # 1: intro paragraph under Cooper.ai
        (ELEMENT_HEADING, 2, 0),  # 2: ## FedEx migration, child of Cooper.ai
        (ELEMENT_LIST_ITEM, 1, 2),  # 3: bullet + its continuation line
        (ELEMENT_LIST_ITEM, 1, 2),  # 4
        (ELEMENT_CODE_BLOCK, None, 2),  # 5: fenced block, one element
        (ELEMENT_BLOCKQUOTE, None, 2),  # 6: both quote lines
        (ELEMENT_HEADING, 2, 0),  # 7: ## Pacifica, sibling section
        (ELEMENT_PARAGRAPH, None, 7),  # 8: owned by Pacifica, NOT FedEx
    ]
    # The wrapped bullet keeps its continuation inside one element.
    assert elements[3].raw_text == "- Removed 195K+ duplicate\n  records"
    # A Result-shaped paragraph with no entity token is still owned by its section —
    # the structural fact the Cooper misassignment lacked.
    assert elements[8].parent_index == 7 and elements[7].parent_index == 0
    assert [e.sequence_index for e in elements] == list(range(len(elements)))


def test_every_element_is_a_verbatim_slice() -> None:
    for element in structure_source_text(_MARKDOWN):
        assert _MARKDOWN[element.raw_start : element.raw_end] == element.raw_text


def test_plain_text_falls_back_to_paragraph_blocks() -> None:
    raw = "First paragraph line one\nline two.\n\nSecond paragraph."
    elements = structure_source_text(raw)
    assert [e.element_type for e in elements] == [ELEMENT_PARAGRAPH, ELEMENT_PARAGRAPH]
    assert all(e.parent_index is None for e in elements)
    assert elements[0].raw_text == "First paragraph line one\nline two."


def test_commit_message_is_one_atomic_element() -> None:
    raw = "Fix the dedupe job\n\nRemoved 195K+ duplicate rows."
    (element,) = structure_commit_message(raw)
    assert element.element_type == ELEMENT_COMMIT_MESSAGE
    assert (element.raw_start, element.raw_end) == (0, len(raw))
    assert element.raw_text == raw
    assert structure_commit_message("") == []
    assert verify_full_coverage(raw, structure_commit_message(raw)) == []


def test_structuring_is_deterministic_and_idempotent() -> None:
    assert structure_source_text(_MARKDOWN) == structure_source_text(_MARKDOWN)


def test_full_coverage_over_the_golden_corpus_and_roster_fixtures() -> None:
    """The reconciliation invariant, property-tested over every text fixture we have:
    zero non-whitespace characters outside the element tree."""
    fixture_files = sorted(CORPUS_DIR.glob("*.txt"))
    roster_fixtures = Path(__file__).parent / "fixtures" / "roster"
    fixture_files += sorted(roster_fixtures.rglob("*.txt")) + sorted(roster_fixtures.rglob("*.md"))
    assert len(fixture_files) >= 8
    for path in fixture_files:
        raw = path.read_text(encoding="utf-8")
        violations = verify_full_coverage(raw, structure_source_text(raw))
        assert violations == [], f"{path.name}: {violations}"


# --- reflow-aware grouping (H5.1): the live word-shred regression ----------------------

_PDF_DAMAGE = (
    "Automated a logistics-reporting pipeline that converts multi-tab Excel\n"
    "datasets,\n"
    " \n"
    "loads\n"
    " \n"
    "five\n"
    " \n"
    "production\n"
    " \n"
    "staging\n"
    " \n"
    "tables,\n"
    " \n"
    "and\n"
    " \n"
    "enables\n"
    " \n"
    "daily\n"
    " \n"
    "analytics/AI\n"
    " \n"
    "refreshes.\n"
    "\n"
    "A real paragraph with terminal punctuation keeps its own break.\n"
)


def test_word_per_line_damage_stays_one_paragraph_element() -> None:
    """The first live H5 run turned each PDF word into an element ('schema',
    'design,', '~$8M'...). Soft blanks between fragments must reflow into the
    governing paragraph — normalizer parity — while spans stay raw and verbatim."""
    elements = structure_source_text(_PDF_DAMAGE)
    assert [e.element_type for e in elements] == [ELEMENT_PARAGRAPH, ELEMENT_PARAGRAPH]
    damaged, clean = elements
    # Every fragment landed inside the first element; none became its own element.
    assert "refreshes." in damaged.raw_text and damaged.raw_text.startswith("Automated")
    assert clean.raw_text == "A real paragraph with terminal punctuation keeps its own break."
    for element in elements:
        assert _PDF_DAMAGE[element.raw_start : element.raw_end] == element.raw_text
    assert verify_full_coverage(_PDF_DAMAGE, elements) == []


def test_hard_paragraph_breaks_survive_reflow() -> None:
    """Multi-word prose ending in terminal punctuation is a real paragraph break —
    the reflow rule must not glue genuine paragraphs together."""
    raw = "First real paragraph ends here.\n\nSecond real paragraph stands alone.\n"
    elements = structure_source_text(raw)
    assert [e.raw_text for e in elements] == [
        "First real paragraph ends here.",
        "Second real paragraph stands alone.",
    ]


def test_bullet_absorbs_flush_left_continuation() -> None:
    """A wrapped bullet whose continuation lands at column 0 (PDF extraction) stays
    one list item — the mid-clause truncation the live run exhibited — while a blank
    line still ends the item (a bullet is structure; no blank after it is soft)."""
    raw = (
        "- Reverse-engineered an unmapped 250-table Oracle database, discovering\n"
        "relationships, and data dependencies.\n"
        "\n"
        "next\n"
    )
    elements = structure_source_text(raw)
    assert elements[0].element_type == ELEMENT_LIST_ITEM
    assert elements[0].raw_text.endswith("relationships, and data dependencies.")
    assert elements[1].raw_text == "next"
    assert verify_full_coverage(raw, elements) == []


# --- the verifier's own teeth (negative controls) --------------------------------------


def _element(seq: int, start: int, end: int, raw: str) -> SourceElement:
    return SourceElement(
        sequence_index=seq,
        element_type=ELEMENT_PARAGRAPH,
        raw_start=start,
        raw_end=end,
        raw_text=raw[start:end],
    )


def test_verifier_catches_dropped_characters() -> None:
    raw = "kept text\ndropped text"
    violations = verify_full_coverage(raw, [_element(0, 0, 9, raw)])
    assert any("uncovered" in v for v in violations)


def test_verifier_catches_overlaps_and_bad_slices() -> None:
    raw = "abcdef\nghijkl"
    overlapping = [_element(0, 0, 8, raw), _element(1, 4, 13, raw)]
    assert any("overlap" in v for v in verify_full_coverage(raw, overlapping))

    bad_slice = SourceElement(
        sequence_index=0,
        element_type=ELEMENT_PARAGRAPH,
        raw_start=0,
        raw_end=13,
        raw_text="not the slice",
    )
    assert any("not its slice" in v for v in verify_full_coverage(raw, [bad_slice]))


def test_verifier_accepts_whitespace_gaps_only() -> None:
    raw = "para one\n\npara two"
    elements = [_element(0, 0, 8, raw), _element(1, 10, 18, raw)]
    assert verify_full_coverage(raw, elements) == []


# --- the version gate (twin of test_normalization_versioning) --------------------------


def test_structurer_version_matches_blessed_digest() -> None:
    blessed = read_blessed()
    assert blessed["structurer_version"] == STRUCTURER_VERSION, (
        f"STRUCTURER_VERSION is {STRUCTURER_VERSION} but the blessed digest was produced "
        f"by v{blessed['structurer_version']}. After a deliberate rule change, re-bless: "
        "python -m app.tools.structure_digest --bless"
    )
    computed = compute_structure_digest(CORPUS_DIR)
    assert computed == blessed["digest"], (
        "structure_source_text output changed over the golden corpus but "
        f"STRUCTURER_VERSION is still {STRUCTURER_VERSION}. Persisted source_elements "
        "record the structurer generation, so a rule change MUST bump the version "
        "(app/domain/source_structure.py) and re-bless: "
        "python -m app.tools.structure_digest --bless"
    )


def test_structure_digest_file_is_committed() -> None:
    assert DIGEST_FILE.exists(), "structure_digest.json missing — bless it and commit it"
