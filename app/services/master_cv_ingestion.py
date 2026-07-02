"""Master CV ingestion service — bridges Drive/GitHub clients to the domain builder.

Flow (per source type):

    list candidates  ->  apply policy  ->  read + record provenance
                                            ->  build PAR-framed Master CV

The service depends only on the :class:`DriveClient` / :class:`GitHubClient` *interfaces*
(injected), so it runs identically against the mocks or the MCP-backed clients. The
domain layer never sees a client — it only receives :class:`CvSource` provenance records.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.domain.cv import CvSource, MasterCv, MasterCvBuilder
from app.integrations.base import DriveClient, GitHubClient, GitHubRepoMetadata
from app.services.source_policy import apply_repo_policy, apply_source_policy

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


def _render_repo_signals(meta: GitHubRepoMetadata) -> str:
    """Render contribution signals as a factual evidence footer (never invented)."""
    parts = []
    if meta.languages:
        parts.append(f"Languages: {', '.join(meta.languages)}")
    parts.append(f"Stars: {meta.stars} | Forks: {meta.forks}")
    if meta.commit_count is not None:
        parts.append(f"Commits: {meta.commit_count}")
    return "\n\n## Repository signals\n" + "\n".join(parts)


async def ingest_github_sources(
    client: GitHubClient,
    user_id: str,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> list[CvSource]:
    """Discover, policy-filter, read, and record GitHub repositories as evidence.

    Returns one :class:`CvSource` per ingested repo, with the README plus a factual
    contribution-signals footer as the raw text. Forks, private repos, and repos outside
    the user's account are skipped by policy and never read.
    """
    settings = settings or get_settings()
    ingested_at = now or datetime.now(tz=UTC)

    candidates = await client.list_candidate_repos(user_id)
    allowed = apply_repo_policy(candidates, settings)
    skipped = len(candidates) - len(allowed)
    if skipped:
        logger.info("Skipped %d GitHub repo(s) by policy (scope/fork/private).", skipped)

    records: list[CvSource] = []
    for repo in allowed:
        document = await client.read_repo(repo.repo_ref)
        metadata = await client.get_repo_metadata(repo.repo_ref)
        raw_text = document.text + _render_repo_signals(metadata)
        records.append(
            CvSource(
                source_type="github",
                external_ref=repo.repo_ref,
                title=document.title or repo.name,
                mime_type="text/markdown",
                raw_text=raw_text,
                modified_time=document.pushed_at or repo.pushed_at,
                ingested_at=ingested_at,
            )
        )
    return records


async def build_master_cv(
    drive_client: DriveClient,
    github_client: GitHubClient,
    user_id: str,
    settings: Settings | None = None,
    builder: MasterCvBuilder | None = None,
    *,
    now: datetime | None = None,
) -> MasterCv:
    """End-to-end: ingest Drive + GitHub evidence and build one PAR-framed Master CV."""
    settings = settings or get_settings()
    drive_sources = await ingest_drive_sources(drive_client, user_id, settings, now=now)
    github_sources = await ingest_github_sources(github_client, user_id, settings, now=now)
    builder = builder or MasterCvBuilder()
    return builder.build([*drive_sources, *github_sources])
