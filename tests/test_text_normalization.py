"""Source text normalization: the mangled-PDF repairs that precede extraction."""

from __future__ import annotations

from app.domain.text_normalization import normalize_source_text


def test_word_per_line_pdf_text_reflows_into_prose() -> None:
    """The audit's Exhibit D shape: a whitespace-only line between every word."""
    mangled = (
        "Reconstructed  the  data  architecture powering the platform, replacing\n \n"
        "a\n \nfragmented\n \nlegacy\n \nExcel\n \nworkflow\n \nacross teams."
    )
    normalized = normalize_source_text(mangled)
    assert normalized == (
        "Reconstructed the data architecture powering the platform, replacing "
        "a fragmented legacy Excel workflow across teams."
    )


def test_line_break_hyphenation_is_joined() -> None:
    assert normalize_source_text("built a Snow-\nflake-ready export layer") == (
        "built a Snowflake-ready export layer"
    )


def test_real_paragraph_breaks_survive() -> None:
    text = "First paragraph ends here.\n\nSecond paragraph starts fresh."
    assert normalize_source_text(text) == (
        "First paragraph ends here.\n\nSecond paragraph starts fresh."
    )


def test_bullets_labels_and_headers_keep_their_own_lines() -> None:
    text = (
        "# Case study\n"
        "Problem: Manual reconciliation burned ten hours\nweekly across the team.\n"
        "- Automated triage with Python\n- Parallelized the suite\n"
    )
    normalized = normalize_source_text(text)
    assert normalized.splitlines() == [
        "# Case study",
        "Problem: Manual reconciliation burned ten hours weekly across the team.",
        "- Automated triage with Python",
        "- Parallelized the suite",
    ]


def test_wrapped_bullet_continuations_join_their_bullet() -> None:
    text = "- Rebuilt the ingest path over\n  Kafka and Python\n- Next item"
    assert normalize_source_text(text).splitlines() == [
        "- Rebuilt the ingest path over Kafka and Python",
        "- Next item",
    ]


def test_whitespace_runs_collapse() -> None:
    assert normalize_source_text("too   many\t\tspaces") == "too many spaces"
