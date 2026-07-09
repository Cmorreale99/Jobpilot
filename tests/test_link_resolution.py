"""Link-resolution units: canonical job URLs and evidence source URLs.

(Preserved from the retired V2 fixture-loop test; the loop it lived in drove the
now-removed claim review API.)
"""

from __future__ import annotations

from app.domain.claims import evidence_source_url
from app.domain.jobs import canonical_job_url


def test_canonical_job_url_strips_tracking_only() -> None:
    canonical = canonical_job_url(
        "https://Boards.example.com/jobs/123?gh_jid=42&utm_source=x&fbclid=y#apply"
    )
    assert canonical == "https://boards.example.com/jobs/123?gh_jid=42"
    assert canonical_job_url("not a url") is None
    assert canonical_job_url(None) is None
    assert canonical_job_url("/relative/path") is None


def test_evidence_source_urls_resolve_by_type() -> None:
    assert (
        evidence_source_url("github_commit", "jordanrivera/carrier-etl@c002outcome")
        == "https://github.com/jordanrivera/carrier-etl/commit/c002outcome"
    )
    assert (
        evidence_source_url("github_readme", "jordanrivera/carrier-etl")
        == "https://github.com/jordanrivera/carrier-etl"
    )
    assert evidence_source_url("drive", "drv_recon_001") == (
        "https://drive.google.com/open?id=drv_recon_001"
    )
    assert evidence_source_url("user_attestation", "claim:1:result") is None
    assert evidence_source_url("upload", "notes.md") is None
