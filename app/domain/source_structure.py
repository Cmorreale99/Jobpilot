"""Canonical source structure: raw text → a deterministic element tree (hardening H4).

*Structure is source data.* A document's hierarchy — which heading governs which
paragraphs, how lists nest, what order everything came in — is part of the source
itself, not something a chunker or assigner should re-infer later. This module derives
that structure from the **captured raw text** (H2), deterministically and versioned,
so it is recomputable and its loss modes are visible:

* ``structure_source_text`` scans Markdown-ish/plain raw text into a flat,
  document-ordered list of :class:`SourceElement`\\ s, each carrying its **verbatim
  raw span** (``raw[raw_start:raw_end] == raw_text``), its type, its nesting level,
  and its parent (the governing heading) by sequence index.
* The scanner is **reflow-aware** (H5.1): element grouping makes the same join
  decisions as ``normalize_source_text`` (``domain/text_normalization.py``), so
  word-per-line PDF damage — one- or two-word fragments separated by whitespace-only
  lines — lands inside ONE paragraph element instead of one element per word, and a
  bullet's flush-left continuation lines stay inside the bullet. Spans remain raw
  coordinates; ``normalize(raw[start:end])`` of an element is clean prose. (The first
  live H5 run word-shredded real Drive PDFs precisely because the structurer broke on
  every blank line the normalizer would have reflowed.)
* ``structure_commit_message`` wraps a commit message as a single element.
* ``verify_full_coverage`` is the reconciliation invariant: element spans plus
  whitespace-only separators must account for **every character** of the raw text —
  zero silently dropped characters. Our text scanner satisfies it by construction;
  future parsers (PDF/DOCX, V4/M23) populate the same shapes and face the same check.

``STRUCTURER_VERSION`` follows the ``NORMALIZATION_VERSION`` discipline: bump on ANY
rule change; the golden-corpus digest test (``app/tools/structure_digest.py``) makes
an un-bumped change a red suite.

Pure and deterministic — no I/O, no LLM.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.text_normalization import (
    blank_is_soft,
    is_entry_boundary,
    is_section_heading,
    starts_structure,
)

# BUMP THIS on ANY change to the scanning rules below — persisted elements record the
# structurer generation that produced them, and the digest test enforces the bump.
# v2 (H5.1): reflow-aware grouping — soft-blank word-per-line fragments join their
# paragraph; list items absorb flush-left continuation lines (normalizer parity).
# v3: blank-line-free PDF resume cues (normalizer v2 parity) — a short ALL-CAPS line
# becomes a level-1 heading, and entry title/date lines break the paragraph run, so a
# whole two-column resume no longer collapses into ONE element (the doc-7 defect that
# welded 'Paper recommender system' into a neighboring project's chunk).
STRUCTURER_VERSION = 3

# Element types (documented values, deliberately no CHECK constraint — same policy as
# evidence source types). ``table``/``table_row`` are reserved for the V4 PDF/DOCX
# parsers; the text scanner never emits them.
ELEMENT_HEADING = "heading"
ELEMENT_PARAGRAPH = "paragraph"
ELEMENT_LIST_ITEM = "list_item"
ELEMENT_CODE_BLOCK = "code_block"
ELEMENT_BLOCKQUOTE = "blockquote"
ELEMENT_COMMIT_MESSAGE = "commit_message"

# Per-element dispositions (the H4 acceptance: every element has one, explicitly).
# The deterministic text scanner only ever emits ``ok``; ``unsupported`` and
# ``parser_error`` exist for the V4 binary parsers that share this schema.
STATUS_OK = "ok"
STATUS_UNSUPPORTED = "unsupported"
STATUS_PARSER_ERROR = "parser_error"

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s")
_BULLET_RE = re.compile(r"^(\s*)(?:[-*•·]|\d+[.)])\s+")
_QUOTE_RE = re.compile(r"^ {0,3}>")
_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")


@dataclass(frozen=True)
class SourceElement:
    """One structural element of a raw source document.

    ``raw_start``/``raw_end`` are end-exclusive offsets into the raw text; the
    verbatim-slice invariant ``raw[raw_start:raw_end] == raw_text`` always holds.
    ``parent_index`` is the ``sequence_index`` of the governing element — for a
    heading, the nearest shallower heading; for everything else, the current heading —
    or ``None`` at document top level.
    """

    sequence_index: int
    element_type: str
    raw_start: int
    raw_end: int
    raw_text: str
    level: int | None = None  # heading depth / list nesting (1-based)
    parent_index: int | None = None


def _line_spans(raw: str) -> list[tuple[int, int, str]]:
    """(start, end, content) per line; end excludes the ``\\n`` separator."""
    spans: list[tuple[int, int, str]] = []
    offset = 0
    for line in raw.split("\n"):
        spans.append((offset, offset + len(line), line))
        offset += len(line) + 1
    return spans


def _is_structural(line: str) -> bool:
    return bool(
        _HEADING_RE.match(line)
        or _BULLET_RE.match(line)
        or _QUOTE_RE.match(line)
        or is_section_heading(line)
        or is_entry_boundary(line)
    )


def structure_source_text(raw: str) -> list[SourceElement]:
    """Scan raw document text into its ordered, hierarchy-carrying element list."""
    lines = _line_spans(raw)
    elements: list[SourceElement] = []
    heading_stack: list[tuple[int, int]] = []  # (level, sequence_index)

    def emit(
        element_type: str,
        first_line: int,
        last_line: int,
        *,
        level: int | None = None,
        parent: int | None = None,
    ) -> None:
        start = lines[first_line][0]
        end = lines[last_line][1]
        elements.append(
            SourceElement(
                sequence_index=len(elements),
                element_type=element_type,
                raw_start=start,
                raw_end=end,
                raw_text=raw[start:end],
                level=level,
                parent_index=parent,
            )
        )

    def current_heading() -> int | None:
        return heading_stack[-1][1] if heading_stack else None

    i = 0
    while i < len(lines):
        _, _, content = lines[i]
        if not content.strip():
            i += 1
            continue

        heading = _HEADING_RE.match(content)
        if heading:
            level = len(heading.group(1))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            emit(ELEMENT_HEADING, i, i, level=level, parent=current_heading())
            heading_stack.append((level, len(elements) - 1))
            i += 1
            continue

        if is_section_heading(content):
            # A short ALL-CAPS line is a top-level section heading (v3): the cue
            # blank-line-free PDF resume text actually carries (EXPERIENCE, SKILLS).
            while heading_stack and heading_stack[-1][0] >= 1:
                heading_stack.pop()
            emit(ELEMENT_HEADING, i, i, level=1, parent=current_heading())
            heading_stack.append((1, len(elements) - 1))
            i += 1
            continue

        if _FENCE_RE.match(content):
            j = i + 1
            while j < len(lines) and not _FENCE_RE.match(lines[j][2]):
                j += 1
            last = min(j, len(lines) - 1)  # include the closing fence; tolerate EOF
            emit(ELEMENT_CODE_BLOCK, i, last, parent=current_heading())
            i = last + 1
            continue

        if _QUOTE_RE.match(content):
            j = i
            while j + 1 < len(lines) and _QUOTE_RE.match(lines[j + 1][2]):
                j += 1
            emit(ELEMENT_BLOCKQUOTE, i, j, parent=current_heading())
            i = j + 1
            continue

        bullet = _BULLET_RE.match(content)
        if bullet:
            indent = len(bullet.group(1))
            j = i
            # Continuation lines: any non-blank, non-structural line (normalizer
            # parity: a flush-left wrapped line after a bullet joins the bullet). A
            # blank line always ends a list item — the bullet began as structure, so
            # no blank after it is ever "soft".
            while j + 1 < len(lines):
                nxt = lines[j + 1][2]
                if not nxt.strip() or _is_structural(nxt) or _FENCE_RE.match(nxt):
                    break
                j += 1
            emit(ELEMENT_LIST_ITEM, i, j, level=1 + indent // 2, parent=current_heading())
            i = j + 1
            continue

        # Paragraph: consecutive non-blank, non-structural lines — PLUS soft-blank
        # continuations (normalizer parity): in word-per-line PDF damage a
        # whitespace-only line sits between fragments, and those fragments belong to
        # this paragraph, not to one element each. ``acc`` mirrors the normalizer's
        # accumulated output line so both modules make the same join decision.
        j = i
        acc = " ".join(content.split())
        acc_structural = starts_structure(content)
        while True:
            k = j + 1
            while k < len(lines) and not lines[k][2].strip():
                k += 1  # skip blank lines to the next candidate
            if k >= len(lines):
                break
            nxt = lines[k][2]
            if _is_structural(nxt) or _FENCE_RE.match(nxt):
                break
            collapsed = " ".join(nxt.split())
            if k > j + 1:
                # Blank line(s) between: join only when the blank is soft junk.
                if starts_structure(nxt) or not blank_is_soft(acc, acc_structural, collapsed):
                    break
                acc = f"{acc} {collapsed}"
            elif starts_structure(nxt):
                # A ``label:`` line starts a fresh output line (same paragraph).
                acc = collapsed
                acc_structural = True
            else:
                acc = f"{acc} {collapsed}"
            j = k
        emit(ELEMENT_PARAGRAPH, i, j, parent=current_heading())
        i = j + 1

    return elements


def structure_commit_message(raw: str) -> list[SourceElement]:
    """A commit message is one atomic element covering the whole raw text."""
    if not raw:
        return []
    return [
        SourceElement(
            sequence_index=0,
            element_type=ELEMENT_COMMIT_MESSAGE,
            raw_start=0,
            raw_end=len(raw),
            raw_text=raw,
        )
    ]


# --- section views over the tree (H5) -------------------------------------------------
# Read-only derivations for section-scoped ownership; they change no scanning rule and
# therefore never require a STRUCTURER_VERSION bump.


def heading_text(element: SourceElement) -> str:
    """A heading's human text: the raw line minus its ``#`` marker."""
    return element.raw_text.lstrip(" #").strip()


def heading_trail(elements: Sequence[SourceElement], index: int) -> str | None:
    """The heading trail governing one element, root-first (``"Cooper.ai > FedEx"``).

    A heading's trail includes itself; anything else starts from its governing
    heading. ``None`` when no heading governs the element (preamble/structureless).
    """
    trail: list[str] = []
    start = elements[index]
    current: SourceElement | None = start
    if start.element_type != ELEMENT_HEADING:
        current = elements[start.parent_index] if start.parent_index is not None else None
    while current is not None:
        trail.append(heading_text(current))
        current = elements[current.parent_index] if current.parent_index is not None else None
    if not trail:
        return None
    return " > ".join(reversed(trail))


@dataclass(frozen=True)
class SectionSubtree:
    """One top-level section of a document: a root heading and everything it governs.

    ``heading_index`` is the root heading's ``sequence_index`` — ``None`` for the
    preamble pseudo-section (elements before any heading). ``path`` is the root
    heading's text. This is the unit of ownership (H5): one entity decision per
    subtree, inherited by every chunk cut from it.
    """

    heading_index: int | None
    path: str | None
    element_indices: tuple[int, ...]


def top_level_sections(elements: Sequence[SourceElement]) -> list[SectionSubtree]:
    """Partition the tree into top-level section subtrees, document order.

    Every element lands in exactly one subtree: the root of its parent chain when
    that root is a heading, else the preamble. A document with no headings is one
    preamble section (the structureless case — per-chunk assignment applies).
    """

    def root_heading(index: int) -> int | None:
        current = elements[index]
        while current.parent_index is not None:
            current = elements[current.parent_index]
        return current.sequence_index if current.element_type == ELEMENT_HEADING else None

    grouped: dict[int | None, list[int]] = {}
    order: list[int | None] = []
    for element in elements:
        root = root_heading(element.sequence_index)
        if root not in grouped:
            grouped[root] = []
            order.append(root)
        grouped[root].append(element.sequence_index)
    return [
        SectionSubtree(
            heading_index=root,
            path=heading_text(elements[root]) if root is not None else None,
            element_indices=tuple(grouped[root]),
        )
        for root in order
    ]


def verify_full_coverage(raw: str, elements: list[SourceElement]) -> list[str]:
    """The reconciliation invariant: zero silently dropped characters.

    Element spans must be in-bounds, non-overlapping, in document order, slice-exact,
    and every uncovered gap (before, between, after) must be whitespace-only. Returns
    human-readable violations; empty means the tree fully accounts for the raw text.
    """
    violations: list[str] = []
    cursor = 0
    for element in sorted(elements, key=lambda e: e.raw_start):
        if element.raw_start < cursor:
            violations.append(
                f"element {element.sequence_index} overlaps previous coverage "
                f"(starts {element.raw_start}, cursor {cursor})"
            )
        if element.raw_end > len(raw) or element.raw_start > len(raw):
            violations.append(f"element {element.sequence_index} span exceeds raw length")
            continue
        if raw[element.raw_start : element.raw_end] != element.raw_text:
            violations.append(f"element {element.sequence_index} raw_text is not its slice")
        gap = raw[cursor : element.raw_start]
        if gap.strip():
            violations.append(
                f"non-whitespace characters uncovered at {cursor}-{element.raw_start}: {gap!r:.80}"
            )
        cursor = max(cursor, element.raw_end)
    tail = raw[cursor:]
    if tail.strip():
        violations.append(f"non-whitespace characters uncovered at tail {cursor}: {tail!r:.80}")
    return violations


__all__ = [
    "ELEMENT_BLOCKQUOTE",
    "ELEMENT_CODE_BLOCK",
    "ELEMENT_COMMIT_MESSAGE",
    "ELEMENT_HEADING",
    "ELEMENT_LIST_ITEM",
    "ELEMENT_PARAGRAPH",
    "STATUS_OK",
    "STATUS_PARSER_ERROR",
    "STATUS_UNSUPPORTED",
    "STRUCTURER_VERSION",
    "SectionSubtree",
    "SourceElement",
    "heading_text",
    "heading_trail",
    "structure_commit_message",
    "structure_source_text",
    "top_level_sections",
    "verify_full_coverage",
]
