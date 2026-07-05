"""Citation-sized chunking of normalized source text, with char spans.

A chunk cited as evidence must be small enough that "the quote appears in the chunk"
means something — a 17 KB document is not a citation (docs/V2_AUDIT.md). This module
splits *normalized* text (see ``domain/text_normalization.py``) into paragraph-aligned
chunks of at most ``max_chars``, each carrying its exact character span into the
normalized text, so ``text[chunk.start : chunk.end] == chunk.text`` always holds.

Rules:

* One chunk per paragraph (blank-line separated) — deliberately NOT packed together:
  a paragraph is the smallest unit that can belong to a different project than its
  neighbors, and project assignment happens per chunk.
* An oversized paragraph splits at line boundaries; an oversized single line splits at
  the last space before the limit (never mid-word unless a "word" exceeds the limit).
* Spans are offsets into the input text, end-exclusive.

Pure and deterministic — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

# Citation-sized: large enough for a paragraph or a few bullets, small enough that a
# verbatim quote inside it is meaningful provenance.
MAX_CHUNK_CHARS = 1200


@dataclass(frozen=True)
class TextChunk:
    """One chunk with its exact char span into the source text (end-exclusive)."""

    start: int
    end: int
    text: str


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Spans of blank-line-separated paragraphs (offsets into ``text``)."""
    spans: list[tuple[int, int]] = []
    offset = 0
    for block in text.split("\n\n"):
        stripped = block.strip("\n")
        if stripped:
            start = offset + (len(block) - len(block.lstrip("\n")))
            spans.append((start, start + len(stripped)))
        offset += len(block) + 2  # the two chars of the "\n\n" separator
    return spans


def _split_oversized(text: str, start: int, limit: int) -> list[tuple[int, int]]:
    """Split one oversized paragraph span at line, then space, boundaries."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        remaining = text[cursor:]
        if len(remaining) <= limit:
            spans.append((start + cursor, start + len(text)))
            break
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit  # a single "word" longer than the limit: hard cut
        spans.append((start + cursor, start + cursor + cut))
        cursor += cut
        while cursor < len(text) and text[cursor] in ("\n", " "):
            cursor += 1  # the separator belongs to no chunk
    return spans


def chunk_normalized_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[TextChunk]:
    """Split normalized text into citation-sized chunks with exact char spans."""
    spans: list[tuple[int, int]] = []
    for start, end in _paragraph_spans(text):
        if end - start > max_chars:
            spans.extend(_split_oversized(text[start:end], start, max_chars))
        else:
            spans.append((start, end))
    return [TextChunk(start=s, end=e, text=text[s:e]) for s, e in spans]


__all__ = ["MAX_CHUNK_CHARS", "TextChunk", "chunk_normalized_text"]
