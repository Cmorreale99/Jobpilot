"""Chunking: paragraph-aligned, span-exact, citation-sized."""

from __future__ import annotations

from app.domain.chunking import chunk_normalized_text


def test_one_chunk_per_paragraph_with_exact_spans() -> None:
    text = "First paragraph here.\n\nSecond paragraph follows.\n\nThird one."
    chunks = chunk_normalized_text(text)
    assert [c.text for c in chunks] == [
        "First paragraph here.",
        "Second paragraph follows.",
        "Third one.",
    ]
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text  # spans are exact


def test_multiline_paragraph_stays_one_chunk() -> None:
    text = "Problem: it was slow.\nAction: fixed it with Python.\n\nNext paragraph."
    chunks = chunk_normalized_text(text)
    assert len(chunks) == 2
    assert "Problem" in chunks[0].text and "Action" in chunks[0].text


def test_oversized_paragraph_splits_at_line_then_space_boundaries() -> None:
    lines = [f"line {i} of the very long paragraph body" for i in range(50)]
    text = "\n".join(lines)
    chunks = chunk_normalized_text(text, max_chars=200)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
    # Nothing was lost: every line survives in exactly one chunk.
    rejoined = " ".join(c.text.replace("\n", " ") for c in chunks)
    assert all(line in rejoined for line in lines)


def test_oversized_single_line_never_splits_mid_word() -> None:
    text = " ".join(["reconciliation"] * 60)
    chunks = chunk_normalized_text(text, max_chars=100)
    assert all(len(c.text) <= 100 for c in chunks)
    assert all(piece == "reconciliation" for c in chunks for piece in c.text.split())


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_normalized_text("") == []
