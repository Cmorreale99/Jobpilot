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

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.source_structure import SourceElement

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


# --- element-derived chunking (H5) ----------------------------------------------------
# Structure-aware chunking: a chunk is one source element, or a piece of one — NEVER a
# span across element boundaries. Spans are offsets into the version's immutable RAW
# text (H2), so chunk identity survives normalizer changes.

# Sentence boundary: terminal punctuation (optionally closing quotes/parens) before
# whitespace — the preferred split point inside an oversized element.
_SENTENCE_END_RE = re.compile(r"[.!?][)\"”']*\s")


@dataclass(frozen=True)
class ElementChunk:
    """One chunk cut from a single source element.

    ``element_index`` is the element's ``sequence_index``; ``raw_start``/``raw_end``
    are offsets into the document version's raw text and ``text`` is that verbatim
    slice (``raw[raw_start:raw_end] == text``). Normalization is the caller's step.
    """

    element_index: int
    raw_start: int
    raw_end: int
    text: str


def _split_sentences(text: str, start: int, limit: int) -> list[tuple[int, int]]:
    """Split one oversized element's raw slice at sentence, then line/space, bounds."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        remaining = text[cursor:]
        if len(remaining) <= limit:
            spans.append((start + cursor, start + len(text)))
            break
        window = remaining[:limit]
        cut = 0
        for match in _SENTENCE_END_RE.finditer(window):
            cut = match.end() - 1  # keep the punctuation, drop the trailing space
        if cut <= 0:
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


def chunk_elements(
    elements: Sequence[SourceElement], *, max_chars: int = MAX_CHUNK_CHARS
) -> list[ElementChunk]:
    """Cut citation-sized chunks from source elements, never across a boundary.

    One chunk per element when it fits; an oversized element splits at sentence
    boundaries within its own raw slice. Whitespace-only elements yield nothing.
    """
    chunks: list[ElementChunk] = []
    for element in elements:
        if not element.raw_text.strip():
            continue
        if len(element.raw_text) <= max_chars:
            spans = [(element.raw_start, element.raw_end)]
        else:
            spans = _split_sentences(element.raw_text, element.raw_start, max_chars)
        offset = element.raw_start
        for span_start, span_end in spans:
            chunks.append(
                ElementChunk(
                    element_index=element.sequence_index,
                    raw_start=span_start,
                    raw_end=span_end,
                    text=element.raw_text[span_start - offset : span_end - offset],
                )
            )
    return chunks


__all__ = [
    "MAX_CHUNK_CHARS",
    "ElementChunk",
    "TextChunk",
    "chunk_elements",
    "chunk_normalized_text",
]
