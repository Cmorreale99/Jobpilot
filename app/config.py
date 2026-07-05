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

    # --- Core / persistence / secrets ---------------------------------------------
    # Default to an in-memory SQLite DB so the app imports and tests run with zero
    # infrastructure. Production sets a Postgres URL (postgresql+psycopg://...).
    database_url: str = "sqlite+pysqlite:///:memory:"
    secret_key: str = ""
    # Fernet key for encrypting OAuth tokens at rest. Generate with
    # ``python -m app.security.crypto``. Required only when the credential store is used.
    token_encryption_key: str = ""

    # --- Google Drive MCP wiring (real integration; off by default) ---------------
    gdrive_mcp_enabled: bool = False
    gdrive_mcp_server: str = ""
    gdrive_mcp_transport: str = "http"
    # stdio transport: the command (+ space-separated args) that launches the server.
    gdrive_mcp_command: str = ""
    gdrive_mcp_args: str = ""

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

    # --- GitHub MCP wiring (real integration; off by default) ---------------------
    github_mcp_enabled: bool = False
    github_mcp_server: str = ""
    github_mcp_transport: str = "http"
    # stdio transport: the command (+ space-separated args) that launches the server.
    github_mcp_command: str = ""
    github_mcp_args: str = ""
    # Dev-only PAT fallback. In production the token comes from the encrypted
    # ``oauth_credentials`` store, decrypted per request — not from static config.
    github_mcp_access_token: str = ""

    # --- GitHub source scoping / safety -------------------------------------------
    # Scope to the user's own repositories; do not scan all of GitHub by default.
    github_username: str = ""
    github_include_forks: bool = False
    github_include_private: bool = False
    github_allow_broad_scan: bool = False

    # --- Mock GitHub fixtures (used when MCP is disabled) --------------------------
    github_mock_fixtures_dir: str = "tests/fixtures/github"

    # --- Uploads (local user-supplied career artifacts) -----------------------------
    # Empty (the default) disables uploads ingestion entirely. Point it at a folder of
    # text/markdown career artifacts to include them as Master CV evidence.
    uploads_dir: str = ""
    uploads_allowed_mime_types: str = "text/plain,text/markdown"

    # --- Jobs + two-stage matching ------------------------------------------------
    shortlist_size: int = 250  # stage-1 shortlist size
    top_n: int = 10  # deep-ranked final matches
    jobs_mock_fixtures_dir: str = "tests/fixtures/jobs"
    # Which job source feeds the pipeline: "mock" (fixtures, default) or "remotive"
    # (compliant public API — keyless, documented, permits listing reuse).
    job_source_provider: str = "mock"
    remotive_api_url: str = "https://remotive.com/api/remote-jobs"
    remotive_search: str = ""  # optional keyword filter passed to the API
    remotive_limit: int = 200

    # --- Mail (Gmail send; only ever behind approval) --------------------------------
    # False (default) selects the in-process mock outbox — nothing can leave the
    # machine. True selects the real Gmail client; the stored Google credential must
    # then carry the gmail.send scope.
    gmail_enabled: bool = False
    gmail_send_url: str = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    # --- Interview scan (separate nightly job; the ONLY inbox read) ------------------
    # Master off-switch for all inbox reads. False disables the scan entirely.
    interview_inbox_scan: bool = True
    # The scoped search query — only mail matching it is ever read.
    interview_scan_query: str = 'interview OR "phone screen" OR "schedule a call"'
    # How far back the scan looks (re-scans are idempotent).
    interview_scan_since_hours: int = 72
    # When the nightly interview scan fires (local time; separate from the pipeline).
    interview_scan_hour: int = 3
    interview_scan_minute: int = 0
    inbox_mock_fixtures_dir: str = "tests/fixtures/inbox"
    # Swap the heuristic prep-packet generator for the LLM-backed one (DEEP tier).
    # Off by default; same real-client requirement/fallback as the other LLM flags.
    interview_llm_prep: bool = False

    # --- Nightly orchestration ------------------------------------------------------
    # The single JobPilot user the nightly pipeline runs for (single-user system).
    pipeline_user_id: str = "u1"
    # When the nightly application pipeline fires (local time).
    pipeline_hour: int = 2
    pipeline_minute: int = 0
    # How far back the job fetch looks ("fresh roles" window).
    jobs_since_hours: int = 24

    # --- Master CV rendering (V2 M11) ------------------------------------------------
    # The docxtpl template extracted from the real CV — integrated as-is, never edited.
    resume_template_path: str = "templates/resume_template.docx"
    # The user's profile JSON (name/tagline/contact_line/education/skills — the
    # non-claim resume data). Empty disables rendering with a clear error, never a
    # resume with invented header data.
    resume_profile_path: str = ""
    # Where rendered artifacts (context JSON + docx) are written; rows in `artifacts`
    # point into this directory.
    artifacts_dir: str = "var/artifacts"

    # --- Dashboard (Next.js dev server origin allowed to call this API) -------------
    dashboard_origins: str = "http://localhost:3000"

    # --- Tailoring + outreach (approval queue) --------------------------------------
    # When false (the default and the safe path), approved drafts wait in the approval
    # queue; nothing is ever sent automatically. Sending itself lands with the real
    # mail client in M6 — until then this flag only logs a warning if enabled.
    outreach_auto_send: bool = False
    research_mock_fixtures_dir: str = "tests/fixtures/research"

    # --- LLM layer (Anthropic) ----------------------------------------------------
    # Mock-first: with ``llm_enabled=false`` the factory returns the fake client, so the
    # whole pipeline runs offline with no API key. Set true (and an API key) to call the
    # real Messages API. Models are two tiers, env-configurable, never hardcoded in code:
    # BULK = cheap/fast (stage-1 scoring, bulk extraction); DEEP = strong (top-N re-rank,
    # outreach drafting, prep packets).
    llm_enabled: bool = False
    # Swap the deterministic heuristic two-pass claim extractor for the LLM-backed one
    # (BULK tier; pass 1 work statements -> Actions, pass 2 outcome statements ->
    # Results). Off by default; same real-client requirement/fallback as the other
    # LLM flags. Either way the PAR validator gates every extracted claim.
    claims_llm_extraction: bool = False
    # Swap the heuristic two-stage matchers for the LLM-backed ones (stage-1 BULK scorer,
    # stage-2 DEEP reranker). Off by default; same real-client requirement/fallback as
    # the other LLM flags.
    matching_llm_ranking: bool = False
    # Swap the heuristic tailorer + outreach drafter for the LLM-backed ones (DEEP tier).
    # Off by default; same real-client requirement/fallback as the other LLM flags.
    tailoring_llm_drafting: bool = False
    # Swap the heuristic roster proposer/chunk assigner (per-source proposals,
    # alias-overlap assignment) for the LLM-backed ones (roster proposal DEEP, chunk
    # assignment BULK). Off by default; same real-client requirement/fallback as the
    # other LLM flags. Either way a HUMAN confirms the roster before extraction.
    roster_llm_detection: bool = False
    anthropic_api_key: str = ""
    anthropic_model_bulk: str = "claude-sonnet-5"
    anthropic_model_deep: str = "claude-opus-4-8"
    # Ceiling on output tokens per call; individual calls may request fewer.
    anthropic_max_tokens: int = 4096
    # Per-request timeout and transient-error retry budget (handled by the SDK).
    # Extraction calls emit thousands of output tokens; 60s reliably times out on
    # them (observed live), so the budget must cover a full long completion.
    anthropic_timeout_seconds: float = 300.0
    anthropic_max_retries: int = 2

    # --- OAuth authorization flow (obtains tokens for the credential store) --------
    # Google (Drive). Default scopes: identity (for the account email) + read-only Drive.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    google_oauth_scopes: str = "openid email https://www.googleapis.com/auth/drive.readonly"

    # GitHub. Default scopes: read the user's identity + repositories.
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = ""
    github_oauth_scopes: str = "read:user repo"

    @property
    def google_oauth_scope_list(self) -> tuple[str, ...]:
        return tuple(s for s in self.google_oauth_scopes.split() if s)

    @property
    def github_oauth_scope_list(self) -> tuple[str, ...]:
        return tuple(s for s in self.github_oauth_scopes.split() if s)

    @property
    def uploads_allowed_mime_types_set(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.uploads_allowed_mime_types.split(",") if item.strip()
        )

    @property
    def dashboard_origin_list(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in self.dashboard_origins.split(",") if o.strip())

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
