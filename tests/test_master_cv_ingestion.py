"""Master CV ingestion tests: provenance, PAR structuring, no fabrication."""

from __future__ import annotations

from app.config import Settings
from app.domain.cv import HeuristicClaimStructurer, MasterCvBuilder
from app.integrations.mock.drive import MockDriveClient
from app.services.master_cv_ingestion import (
    build_master_cv_from_drive,
    ingest_drive_sources,
)


async def test_ingest_records_provenance(mock_client: MockDriveClient, settings: Settings) -> None:
    sources = await ingest_drive_sources(mock_client, "user-1", settings)
    assert {s.external_ref for s in sources} == {
        "drv_resume_001",
        "drv_project_002",
        "drv_portfolio_003",
        "drv_transcript_004",
    }
    for source in sources:
        assert source.source_type == "gdrive"
        assert source.raw_text.strip()
        assert source.title
        assert source.mime_type
        assert source.ingested_at is not None


async def test_master_cv_has_par_claims(mock_client: MockDriveClient, settings: Settings) -> None:
    cv = await build_master_cv_from_drive(mock_client, "user-1", settings)
    assert cv.claims, "expected PAR claims extracted from fixtures"

    par_claims = [c for c in cv.claims if c.problem and c.result]
    assert par_claims, "expected at least one full Problem/Action/Result claim"

    sample = next(c for c in par_claims if "settlement" in c.action.lower())
    assert sample.problem is not None
    assert sample.result is not None
    assert sample.source_ref == "drv_resume_001"


async def test_every_claim_traces_to_a_source(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    cv = await build_master_cv_from_drive(mock_client, "user-1", settings)
    source_refs = {s.external_ref for s in cv.sources}
    assert source_refs, "expected provenance records"
    for claim in cv.claims:
        assert claim.source_ref in source_refs


async def test_structurer_does_not_invent_content(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    sources = await ingest_drive_sources(mock_client, "user-1", settings)
    structurer = HeuristicClaimStructurer()
    for source in sources:
        for claim in structurer.structure(source):
            # Every claim's action text must appear verbatim in the source text.
            assert claim.action in source.raw_text


async def test_content_json_is_serializable(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    import json

    cv = await build_master_cv_from_drive(mock_client, "user-1", settings)
    payload = cv.to_content_json()
    # Round-trips through JSON (what master_cv.content_json stores).
    json.dumps(payload)
    assert payload["version"] == 1
    assert len(payload["sources"]) == 4


async def test_builder_accepts_injected_structurer(
    mock_client: MockDriveClient, settings: Settings
) -> None:
    sources = await ingest_drive_sources(mock_client, "user-1", settings)

    class NullStructurer:
        def structure(self, source):  # type: ignore[no-untyped-def]
            return []

    cv = MasterCvBuilder(structurer=NullStructurer()).build(sources)
    assert cv.claims == []
    assert len(cv.sources) == 4
