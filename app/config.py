"""Typed, env-driven settings.

Config is read here and nowhere else — no scattered ``os.environ`` reads. Only the
subset of settings needed for the Master CV / Google Drive ingestion slice is defined
so far; other groups (LLM, DB, job sources) get added as their milestones land.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Default MIME allowlist: Google Docs, PDF, DOCX, Markdown, plain text. These are the
# only formats treated as career artifacts. Anything else (images, spreadsheets,
# personal notes) is skipped by policy.
DEFAULT_ALLOWED_MIME_TYPES = (
    "application/pdf,"
    "application/vnd.google-apps.document,"
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
    "text/plain,"
    "text/markdown"
)


class Settings(BaseSettings):
    """Application settings.

    Field names map to upper-case environment variables (e.g. ``gdrive_mcp_enabled``
    <- ``GDRIVE_MCP_ENABLED``). Defaults reflect the safe, mock-first path: MCP is off,
    broad Drive scanning is off, and no real credentials are required.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Google Drive MCP wiring (real integration; off by default) ---------------
    gdrive_mcp_enabled: bool = False
    gdrive_mcp_server: str = ""
    gdrive_mcp_transport: str = "http"

    # Dev-only credential fallback. In production these come from the encrypted
    # ``oauth_credentials`` store (decrypted per request), NOT from static config.
    gdrive_mcp_user_email: str = ""
    gdrive_mcp_access_token: str = ""

    # --- Google Drive source scoping / safety -------------------------------------
    gdrive_source_folder_id: str = ""
    gdrive_allowed_mime_types: str = DEFAULT_ALLOWED_MIME_TYPES
    gdrive_allow_broad_scan: bool = False

    # --- Mock Drive fixtures (used when MCP is disabled) ---------------------------
    gdrive_mock_fixtures_dir: str = "tests/fixtures/drive"

    @property
    def gdrive_allowed_mime_types_set(self) -> frozenset[str]:
        """Allowed MIME types as a set, tolerant of surrounding whitespace."""
        return frozenset(
            item.strip() for item in self.gdrive_allowed_mime_types.split(",") if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
