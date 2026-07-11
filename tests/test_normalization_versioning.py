"""Normalization version enforcement (hardening H3).

``NORMALIZATION_VERSION`` stamps every evidence row and every captured source version,
and ``#chars=`` spans are offsets into the normalizer's OUTPUT — so a rule change that
ships without a version bump silently dangles every stored span. These tests make that
impossible: the blessed digest pins the normalizer's exact output over the pathological
corpus, keyed by the version that produced it.

Deliberate rule change workflow: bump ``NORMALIZATION_VERSION`` in
``app/domain/text_normalization.py``, then re-bless::

    python -m app.tools.normalization_digest --bless
"""

from __future__ import annotations

from app.domain.text_normalization import NORMALIZATION_VERSION, normalize_source_text
from app.tools.normalization_digest import (
    CORPUS_DIR,
    DIGEST_FILE,
    compute_corpus_digest,
    corpus_inputs,
    read_blessed,
)

# --- the version gate -----------------------------------------------------------------


def test_corpus_is_present_and_broad() -> None:
    """An emptied corpus would make the digest test pass vacuously — refuse that."""
    inputs = corpus_inputs()
    assert len(inputs) >= 5, "the normalization corpus lost fixtures — restore them"
    assert all(path.read_text(encoding="utf-8").strip() for path in inputs)


def test_normalizer_version_matches_blessed_digest() -> None:
    """The F4 gate: a rule change without a version bump (or an un-re-blessed bump)
    is a red suite, not a silent span-dangling deploy."""
    blessed = read_blessed()
    assert blessed["normalization_version"] == NORMALIZATION_VERSION, (
        f"NORMALIZATION_VERSION is {NORMALIZATION_VERSION} but the blessed digest was "
        f"produced by v{blessed['normalization_version']}. After a deliberate rule "
        "change, re-bless: python -m app.tools.normalization_digest --bless"
    )
    computed = compute_corpus_digest(CORPUS_DIR)
    assert computed == blessed["digest"], (
        "normalize_source_text output changed over the golden corpus but "
        f"NORMALIZATION_VERSION is still {NORMALIZATION_VERSION}. Every stored "
        "#chars= span points into normalizer output, so a rule change MUST bump the "
        "version (app/domain/text_normalization.py) and re-bless the digest: "
        "python -m app.tools.normalization_digest --bless"
    )


def test_digest_file_is_the_committed_artifact() -> None:
    assert DIGEST_FILE.exists(), "digest.json missing — bless it and commit it"


# --- characterization: documented lossy edges of the current rules (v1) ---------------
#
# These pin CURRENT behavior, deliberately including the known over-eager cases from
# the loss audit (F4 notes). If one of these starts failing, a rule changed — which is
# exactly when the version must bump and the digest re-bless; fixing the behavior is
# welcome, shipping it silently is not.


def test_hyphen_join_repairs_line_break_hyphenation() -> None:
    assert normalize_source_text("Snow-\nflake") == "Snowflake"


def test_hyphen_join_over_deletes_a_real_hyphen_at_a_line_break() -> None:
    # Known v1 limitation: a genuine hyphen that happens to sit at a line end is
    # eaten too. The raw text survives in the capture layer (H2), so this stays
    # recoverable — but the normalized form is what spans point into.
    assert normalize_source_text("state-\nof-the-art") == "stateof-the-art"


def test_soft_blank_merges_two_short_real_paragraphs() -> None:
    # Known v1 limitation: two consecutive short fragments without terminal
    # punctuation read as word-per-line PDF debris and merge, even when they were
    # genuine paragraphs ("Won big" / "Top 3.").
    assert normalize_source_text("Won big\n\nTop 3.") == "Won big Top 3."


def test_real_paragraphs_with_terminal_punctuation_keep_their_break() -> None:
    text = "This sentence ends properly.\n\nAnd this one stays its own paragraph."
    assert normalize_source_text(text) == text  # the break survives verbatim
