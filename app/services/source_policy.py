"""Drive source-selection policy — the single gate for what may be ingested.

Applied by the ingestion service to whatever a :class:`DriveClient` lists, regardless of
whether that client is the mock or the MCP-backed one. Two rules:

* **MIME allowlist** — only career-artifact formats (Docs, PDF, DOCX, Markdown, text)
  pass. Everything else (images, spreadsheets, ...) is skipped.
* **Scope** — do not scan all of Drive by default. Sources must live in the approved
  ``GDRIVE_SOURCE_FOLDER_ID``. Broad scanning happens only when
  ``GDRIVE_ALLOW_BROAD_SCAN`` is explicitly enabled.

Keeping this in the service (not the client) means the safety rule is enforced in one
place and cannot be bypassed by swapping the client implementation.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.config import Settings
from app.integrations.base import DriveSource, GitHubRepo


def is_mime_allowed(mime_type: str, settings: Settings) -> bool:
    """True if ``mime_type`` is on the configured career-artifact allowlist."""
    return mime_type in settings.gdrive_allowed_mime_types_set


def is_in_scope(source: DriveSource, settings: Settings) -> bool:
    """True if ``source`` is within the approved ingestion scope.

    * Broad scan enabled -> everything is in scope (MIME filtering still applies).
    * Otherwise -> the source must sit in the configured approved folder. If no folder
      is configured, nothing is in scope (we never scan the whole Drive by default).
    """
    if settings.gdrive_allow_broad_scan:
        return True
    if not settings.gdrive_source_folder_id:
        return False
    return source.folder_id == settings.gdrive_source_folder_id


def apply_source_policy(sources: Iterable[DriveSource], settings: Settings) -> list[DriveSource]:
    """Filter raw candidate sources down to those safe to ingest."""
    return [
        source
        for source in sources
        if is_mime_allowed(source.mime_type, settings) and is_in_scope(source, settings)
    ]


# --- GitHub repo policy ----------------------------------------------------------
#
# The GitHub analogue of the Drive rules: scope to the user's own account (don't scan
# all of GitHub) and exclude non-original / sensitive repos (forks, private) by default.


def is_repo_in_scope(repo: GitHubRepo, settings: Settings) -> bool:
    """True if ``repo`` is within the approved GitHub ingestion scope.

    * Broad scan enabled -> every repo is in scope.
    * Otherwise -> the repo must be owned by the configured ``GITHUB_USERNAME``. If no
      username is configured, nothing is in scope (never scan all of GitHub by default).
    """
    if settings.github_allow_broad_scan:
        return True
    if not settings.github_username:
        return False
    return repo.owner.lower() == settings.github_username.lower()


def is_repo_kind_allowed(repo: GitHubRepo, settings: Settings) -> bool:
    """True unless the repo is a fork/private that the config has not opted into."""
    if repo.is_fork and not settings.github_include_forks:
        return False
    return not (repo.is_private and not settings.github_include_private)


def apply_repo_policy(repos: Iterable[GitHubRepo], settings: Settings) -> list[GitHubRepo]:
    """Filter raw candidate repos down to those safe to ingest as career evidence."""
    return [
        repo
        for repo in repos
        if is_repo_kind_allowed(repo, settings) and is_repo_in_scope(repo, settings)
    ]
