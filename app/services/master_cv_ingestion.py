"""Master CV ingestion service — bridges a :class:`DriveClient` to the domain builder.

Flow:

    list candidate sources  ->  apply source policy  ->  read + record provenance
                                                          ->  build PAR-framed Master CV

The service depends only on the :class:`DriveClient` *interface* (injected), so it runs
identically against the mock or the MCP-backed client. The domain layer never sees a
``DriveClient`` — it only receives :class:`CvSource` provenance records.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.domain.cv import CvSource, MasterCv, MasterCvBuilder
from app.integrations.base import DriveClient
from app.services.source_policy import apply_source_policy

logger = logging.getLogger(__name__)


async def ingest_drive_sources(
    client: DriveClient,
    user_id: str,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> list[CvSource]:
    """Discover, policy-filter, read, and record Drive career artifacts.

    Returns one :class:`CvSource` provenance record per ingested document. Sources that
    fail the MIME allowlist or scope policy are skipped and logged, never read.
    """
    settings = settings or get_settings()
    ingested_at = now or datetime.now(tz=UTC)

    candidates = await client.list_candidate_sources(user_id)
    allowed = apply_source_policy(candidates, settings)
    skipped = len(candidates) - len(allowed)
    if skipped:
        logger.info("Skipped %d Drive source(s) by policy (mime/scope).", skipped)

    records: list[CvSource] = []
    for source in allowed:
        document = await client.read_source(source.source_ref)
        records.append(
            CvSource(
                source_type="gdrive",
                external_ref=document.source_ref,
                title=document.title,
                mime_type=document.mime_type,
                raw_text=document.text,
                modified_time=document.modified_time,
                ingested_at=ingested_at,
            )
        )
    return records


async def build_master_cv_from_drive(
    client: DriveClient,
    user_id: str,
    settings: Settings | None = None,
    builder: MasterCvBuilder | None = None,
    *,
    now: datetime | None = None,
) -> MasterCv:
    """End-to-end: ingest Drive sources and build a PAR-framed Master CV."""
    sources = await ingest_drive_sources(client, user_id, settings, now=now)
    builder = builder or MasterCvBuilder()
    return builder.build(sources)
