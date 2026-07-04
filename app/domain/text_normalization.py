"""Source text normalization: undo extraction damage before claims are extracted.

Real Drive text arrives mangled — word-per-line PDF extraction (sometimes with a
whitespace-only line between every word) and run-on docx exports. The claim extractors
are line-oriented, so unnormalized text turns into one-word "statements" and fragment
claims (see docs/V2_AUDIT.md, Exhibit B/D). Normalization is the prerequisite for the
structural validators: it repairs layout damage, and anything still malformed after it
is genuinely bad content.

Rules (deterministic, pure):

* Line-break hyphenation is joined: ``Snow-\\n flake`` -> ``Snowflake``.
* Lines that start structure — bullets, ``#`` headers, ``label:`` lines — always begin
  a new line of output.
* Consecutive non-blank lines are wrapped text and join with a space.
* A blank line is a paragraph break — EXCEPT in the word-per-line PDF pathology, where
  a whitespace-only line sits between fragments (``replacing\\n \\na\\n \\nfragmented``).
  A blank is treated as spacing junk and reflowed only when the previous line is
  mid-sentence (no terminal punctuation, not structural) and either side is a one- or
  two-word fragment. Real paragraphs, commit subject/body splits, and heading/body
  splits all keep their break.
* Whitespace runs collapse to single spaces.

Quotes taken from normalized text remain verbatim under the validator's
``verbatim_in`` (which is whitespace-reflow tolerant) — but chunks are normalized
BEFORE extraction, so quotes are taken from and checked against the same text.
"""

from __future__ import annotations

import re

_LINE_HYPHEN_RE = re.compile(r"(\w)-[ \t]*\n[ \t]*(?=\w)")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")
_LABEL_RE = re.compile(r"^\s*[A-Za-z][\w ]{0,24}:\s+\S")
_HEADER_RE = re.compile(r"^\s*#")
_TERMINAL = (".", "!", "?", ":", ";")


_FRAGMENT_TOKENS = 2  # a line this short next to a soft blank is PDF debris, not prose


def _starts_structure(line: str) -> bool:
    return bool(_BULLET_RE.match(line) or _HEADER_RE.match(line) or _LABEL_RE.match(line))


def _blank_is_soft(previous: str, previous_structural: bool, current: str) -> bool:
    """True when a blank line is word-per-line spacing junk rather than a paragraph."""
    if previous_structural or previous.endswith(_TERMINAL):
        return False
    return len(previous.split()) <= _FRAGMENT_TOKENS or len(current.split()) <= _FRAGMENT_TOKENS


def normalize_source_text(text: str) -> str:
    """Reflow mangled source text into line-oriented prose (see module docstring)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _LINE_HYPHEN_RE.sub(r"\1", text)

    lines: list[str] = []
    structural: list[bool] = []  # parallel: whether lines[i] began as structure
    saw_blank = False
    for raw in text.split("\n"):
        line = " ".join(raw.split())
        if not line:
            saw_blank = True
            continue
        is_structure = _starts_structure(raw)
        joins_previous = bool(lines) and not is_structure
        if joins_previous and saw_blank:
            joins_previous = _blank_is_soft(lines[-1], structural[-1], line)
        if joins_previous:
            lines[-1] = f"{lines[-1]} {line}"
        else:
            if lines and saw_blank:
                lines.append("")  # a real paragraph break survives
                structural.append(False)
            lines.append(line)
            structural.append(is_structure)
        saw_blank = False
    return "\n".join(lines)


__all__ = ["normalize_source_text"]
