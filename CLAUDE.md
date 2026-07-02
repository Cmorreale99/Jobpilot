# CLAUDE.md

Operating manual for anyone (human or agent) working in this repo. Read this before writing code. Keep it current — if you change a convention, update this file in the same commit.

## What this is

JobPilot: a personalized, mostly-automated job-search pipeline for a single user. It builds a canonical Master CV from the user's own artifacts, finds fresh high-fit roles, tailors materials, drafts targeted outreach, detects interview invitations, and generates prep packets — surfaced on a dashboard. Optimize for **fit and quality per application, not volume.**

## Commands

```bash
# setup
uv sync                          # install deps
cp .env.example .env             # then fill in values
alembic upgrade head             # apply migrations

# run
uv run uvicorn app.main:app --reload      # API
uv run python -m app.scheduler            # nightly jobs (dev)
cd web && npm run dev                     # dashboard

# quality — run before every commit
uv run ruff check . && uv run ruff format .
uv run mypy app
uv run pytest -q

# migrations
alembic revision --autogenerate -m "message"
alembic upgrade head
```

Everything above must pass on fixtures with **zero real credentials.** If it doesn't, that's a bug.

## How to work here

1. **Plan before building.** Keep `PLAN.md` current with scope, milestones, and open questions.
2. **Interfaces + mocks first.** Every external dependency sits behind an interface with a fake implementation and fixtures. The full pipeline runs offline before any real credential is required. Never wire a real API before its mock and tests exist.
3. **Vertical slices, committed per milestone.** Each milestone is independently runnable.
4. **Test what breaks silently:** CV structuring, fit scoring/ranking, migrations, and the application/interview state machines. Don't chase coverage numbers.
5. **Ask before irreversible or external actions** — sending real email, first-time paid/ToS-sensitive API calls, file deletion. Print the intended action and wait.
6. **No dependency without a one-line justification** added under "Dependencies" below.

## Repo layout

```
app/
  main.py              # FastAPI entrypoint
  config.py            # env-driven settings (pydantic-settings)
  db/                  # SQLAlchemy models, session, Alembic env
  domain/              # pure business logic: matching, ranking, state machines
  services/            # orchestration-level functions the scheduler/API call
  integrations/
    base.py            # interfaces (JobSource, DriveClient, GitHubClient, MailClient, ResearchClient)
    mock/              # fakes + fixtures — the default in dev/tests
    real/              # Gmail, compliant job API, LinkedIn (flagged)
    mcp/               # MCP client sessions: GitHub MCP + Google Drive MCP (back DriveClient/GitHubClient)
  llm/                 # single module for all Anthropic calls (retries, JSON parsing, cost logging)
  scheduler.py         # APScheduler triggers -> service functions
tests/
  fixtures/            # seed CVs, jobs, interview-invite emails
web/                   # Next.js + Tailwind dashboard
PLAN.md
CLAUDE.md
.env.example
```

**Rule:** `domain/` imports nothing from `integrations/real/` or `llm/` directly — it takes interfaces/data as arguments. Business logic stays testable in isolation.

## Stack + conventions

- Python 3.12, `uv`, FastAPI, async for I/O-bound integrations.
- `ruff` (lint + format), `mypy` (typed; annotate public functions), `pytest`.
- SQLAlchemy 2.x (typed models) + Alembic. **Postgres is the transactional source of truth — never a warehouse for app data.**
- Next.js + Tailwind, minimal. One dashboard, a few detail views.
- APScheduler in dev; keep triggers thin so business logic ports to EventBridge→Lambda untouched.
- Naming: `snake_case` Python, `PascalCase` models/classes, table names plural.
- Config via `app/config.py` only — no `os.environ` reads scattered through the code.

## Data model (source of truth: migrations)

`users`, `oauth_credentials` (tokens encrypted at rest), `cv_sources` (provenance), `master_cv` (versioned `content_json`, all PAR-framed), `jobs`, `job_matches`, `applications`, `outreach`, `interviews`, `prep_packets`.

- `applications.status`: `drafted | applied | interviewing | rejected | offer | ignored`
- `interviews.stage` and `outreach.status`: explicit state machines with validated transitions in `domain/`.
- Dedupe jobs on `(source, external_id)`. Nightly jobs are **idempotent** — re-running never double-applies, double-sends, or duplicates rows.
- **Master CV persistence** (`MasterCvRepository`, in-memory + `SqlMasterCvRepository`): each save writes a new `master_cv` version, but idempotently — a content fingerprint that excludes volatile timestamps (`ingested_at`) means an unchanged refresh adds no version. `cv_sources` dedupe on `(user_id, source_type, external_ref)`. `refresh_master_cv` (`services/master_cv_ingestion.py`) is the build-and-persist entrypoint.
- **Uploads** (third evidence source alongside Drive + GitHub): `UploadsClient` interface with `LocalUploadsClient` (`integrations/uploads.py`) over a configured `UPLOADS_DIR` — empty by default, meaning disabled. Text/markdown allowlist (`apply_upload_policy`) filters before any read; ingestion records `CvSource(source_type="upload")` and `build_master_cv` merges all three source types.
- **Jobs + matching**: `JobSource` (mock + future real) feeds `jobs` (deduped on `(source, external_id)`). Two-stage matching (`domain/matching.py`, pure + deterministic) scores every job (stage 1) → shortlists `SHORTLIST_SIZE` → deep re-ranks `TOP_N` with a rationale (stage 2), both behind `JobScorer`/`JobReranker` protocols with heuristic (default) and LLM-backed (`llm/matching.py`, flag `MATCHING_LLM_RANKING`) implementations. `run_matching` (`services/matching.py`) persists results to `job_matches` per `(user_id, master_cv_version)` with replace semantics.
- **Dashboard**: `web/` (Next.js app router + Tailwind v4, TypeScript) renders one "pipeline ledger" page (approval queue → matches → applications → connect accounts) plus an application detail view; it talks to the read API in `app/api/dashboard.py` (`/master-cv/latest`, `/matches`, `/applications[/{id}]`, `POST /applications/{id}/transition`) with repos injected in tests and lazily built over SQL in prod. CORS is restricted to `DASHBOARD_ORIGINS` (default `http://localhost:3000`); the web app reads `NEXT_PUBLIC_API_URL`/`NEXT_PUBLIC_USER_ID` (see `web/.env.example`).
- **Tailoring + outreach (approval queue)**: state machines live in `domain/applications.py` — `applications.status` (`drafted → applied → interviewing → rejected|offer`, `drafted → ignored`) and `outreach.status` (`drafted → approved → sent`, `drafted → discarded`), transitions validated everywhere (`InvalidTransitionError`). Tailoring (`domain/tailoring.py`) and drafting (`domain/outreach.py`) sit behind `MaterialsTailorer`/`OutreachDrafter` protocols — heuristic defaults render Master CV claims verbatim; LLM-backed versions (`llm/drafting.py`, flag `TAILORING_LLM_DRAFTING`, DEEP tier) ground highlights by claim id (hallucinated ids dropped) and fall back to the heuristics on failure. Contacts come from the `ResearchClient` interface (mock fixture-backed) or are honestly absent — never invented. `ApplicationRepository` (in-memory + SQL, migration `0004`) keeps one application per `(user_id, job)` and one outreach row per application; re-runs refresh *drafted* rows only and never overwrite a human decision. `run_drafting` (`services/outreach.py`) orchestrates; the approval queue is served by `GET /outreach/queue` + `POST /outreach/{id}/approve|discard` (`api/outreach.py`).

## LLM layer

All Anthropic Messages API calls go through `app/llm/`. Prompt for **strict JSON** on structured steps; strip fences, validate against a schema, retry once on parse failure. Central retries, timeouts, and token/cost logging live here.

Tiered models, env-configurable, never hardcoded:
- `ANTHROPIC_MODEL_BULK` — stage-1 scoring + bulk extraction (cheap/fast).
- `ANTHROPIC_MODEL_DEEP` — top-10 re-rank, outreach drafting, prep packets (strong).

## Secrets & OAuth credential store

OAuth tokens are **encrypted at rest**. `app/security/crypto.py` (`TokenCipher`, Fernet,
keyed by `TOKEN_ENCRYPTION_KEY` — generate with `python -m app.security.crypto`) is the
only place tokens are encrypted/decrypted. The `oauth_credentials` table
(`app/db/models.py`) stores ciphertext only.

The `OAuthCredentialStore` protocol (`app/domain/credentials.py`) trades in decrypted
`OAuthCredential` values; implementations encrypt on write. Two exist:
`InMemoryOAuthCredentialStore` (mock-first, `app/services/credentials.py`) and
`SqlOAuthCredentialStore` (`app/db/credentials_store.py`). The Drive/GitHub factories take
a `store` + `user_id` and inject decrypted `DriveCredentials`/`GitHubCredentials` into the
MCP clients — so credentials flow from the encrypted store to the client without the rest
of the app seeing plaintext or ciphertext.

**Authorization flow** (`app/integrations/oauth/`, `app/services/oauth_flow.py`): the
`OAuthProvider` interface (mock + real `GoogleOAuthProvider`/`GitHubOAuthProvider` over
httpx) plus `OAuthFlowService`, which does `start` (mint anti-CSRF `state`, return consent
URL) → `complete` (validate single-use `state`, exchange code, encrypted upsert) →
`get_valid_credential` (auto-refresh near-expiry tokens). Providers do no storage; the
flow does no HTTP.

**HTTP** (`app/main.py`, `app/api/oauth.py`): `GET /oauth/{provider}/start` redirects
(302) to consent; `/callback` validates the single-use `state`, exchanges the code, and
upserts the encrypted credential. The `OAuthFlowService` is a per-app singleton (the
state store links start↔callback), injected in tests and built lazily in prod — a missing
`TOKEN_ENCRYPTION_KEY` surfaces as HTTP 503, not a crash. `app.main:app` imports with zero
credentials.

**Migrations:** schema is owned by Alembic (`alembic.ini`, env under `app/db/migrations/`;
the URL is resolved from `DATABASE_URL`). Run `alembic upgrade head`; add revisions with
`alembic revision --autogenerate -m "..."`. `create_all` is only a dev/test convenience.

## MCP integrations (GitHub + Google Drive)

GitHub and Google Drive are accessed through their **MCP servers**, not hand-rolled REST clients. The `real/` implementations of `GitHubClient` and `DriveClient` open an MCP client session (code in `integrations/mcp/`) and call the server's tools to list/read repos, READMEs, contribution signals, and Drive docs/PDFs; extracted content is then handed to `llm/` for PAR structuring. The same tools can also be surfaced directly to the agent for exploratory extraction — either way the boundary is the same.

- Interfaces and mocks are **unchanged**: `MockGitHubClient` / `MockDriveClient` still front the pipeline, so it runs offline on fixtures and `domain/` stays decoupled from MCP.
- Configure server endpoints/commands via `GITHUB_MCP_SERVER` and `GDRIVE_MCP_SERVER` in `.env`.
- OAuth still governs the underlying access; tokens are passed to the MCP servers and stored encrypted per the guardrails.
- Gmail (send + scoped interview scan), the compliant job source, and LinkedIn remain direct API clients — MCP is only for Drive + GitHub.

### Google Drive integration (Master CV evidence)

Drive is an **evidence source for explicit career artifacts only** — resumes, project
docs, portfolio/case-study docs, transcripts, work samples (PDF/DOCX/Google Docs/
Markdown/text). It is **read/search/download only.** Broad personal Drive mining is out
of scope; the `DriveClient` interface deliberately exposes no delete/move/permission/
write operations.

- **Mocks are the default.** `GDRIVE_MCP_ENABLED=false` selects `MockDriveClient`
  (`app/integrations/mock/drive.py`), backed by `tests/fixtures/drive/`. The whole
  Master CV pipeline runs end-to-end with no OAuth, no MCP, no network.
- **Interface:** `DriveClient` in `app/integrations/base.py` —
  `list_candidate_sources`, `read_source`, `get_source_metadata`, `list_changed_sources`.
  Nothing broader.
- **Real client:** `McpDriveClient` (`app/integrations/mcp/drive.py`) is an async adapter
  onto the **Google Workspace MCP** server (taylorwilsdon/google_workspace_mcp), mapping
  the interface to its `list_drive_items` / `search_drive_files` / `get_drive_file_content`
  / `get_drive_file_permissions` tools. Endpoint/transport come from config only
  (`GDRIVE_MCP_SERVER`, `GDRIVE_MCP_TRANSPORT`, `GDRIVE_MCP_ENABLED`) — no URLs hardcoded.
  On connect it validates tool names via `list_tools` and fails with a clear
  `DriveConfigurationError`. The `DriveClient` interface is **async** (mock included);
  the ingestion service awaits it. Remaining to go live: the encrypted OAuth token store
  (`TODO(mcp-auth)`) and, if the server returns formatted text rather than structured
  content, a parser at `_extract_payload` — see PLAN.md M6.
- **Selection:** `create_drive_client()` (`app/integrations/drive_factory.py`) returns
  mock vs. MCP based on `GDRIVE_MCP_ENABLED`.
- **Source policy** (`app/services/source_policy.py`) is the single ingestion gate:
  MIME allowlist (`GDRIVE_ALLOWED_MIME_TYPES`) + approved-folder scope
  (`GDRIVE_SOURCE_FOLDER_ID`). Broad scanning is off unless `GDRIVE_ALLOW_BROAD_SCAN=true`;
  with no folder and no broad scan, nothing is ingested.
- **Provenance:** ingestion (`app/services/master_cv_ingestion.py`) records a `CvSource`
  per document (`source_type=gdrive`, external ref, title, mime, modified time, raw text,
  ingested-at). The domain `MasterCvBuilder` structures evidence into PAR claims that
  each trace back to a source ref — it never invents experience.

### GitHub integration (Master CV evidence)

Mirrors the Drive design. GitHub is a **read-only career-evidence source**: the user's own
repositories, their READMEs, and contribution signals. The `GitHubClient` interface
(`list_candidate_repos`, `read_repo`, `get_repo_metadata`, `list_changed_repos`) exposes
no issue/PR/write operations.

- **Mocks are the default.** `GITHUB_MCP_ENABLED=false` selects `MockGitHubClient`
  (`app/integrations/mock/github.py`), backed by `tests/fixtures/github/`.
- **Real client:** `McpGitHubClient` (`app/integrations/mcp/github.py`) is an async adapter
  onto the official **github/github-mcp-server**, mapping the interface to
  `search_repositories` / `get_file_contents` / `list_commits`. Endpoint/transport from
  config only (`GITHUB_MCP_SERVER`, `GITHUB_MCP_TRANSPORT`, `GITHUB_MCP_ENABLED`); tool
  names validated via `list_tools`; auth is a PAT (`GitHubCredentials`). Selection via
  `create_github_client()` (`app/integrations/github_factory.py`).
- **Repo policy** (`app/services/source_policy.py`, `apply_repo_policy`): scope to
  `GITHUB_USERNAME` and exclude forks/private unless `GITHUB_INCLUDE_FORKS` /
  `GITHUB_INCLUDE_PRIVATE` are set. Broad scanning off unless `GITHUB_ALLOW_BROAD_SCAN=true`;
  with no username and no broad scan, nothing is ingested.
- **Provenance:** each repo becomes a `CvSource` (`source_type=github`, `repo_ref` ref,
  README + factual signals footer). `build_master_cv()` merges Drive + GitHub evidence
  into one PAR-framed Master CV; claims trace to their `repo_ref`.

## Pipeline (two independent nightly jobs)

**Application pipeline:** refresh Master CV (if sources changed) → fetch jobs (24h) → score all → top `SHORTLIST_SIZE` (~250) → deep re-rank to `TOP_N` (10) with rationale → tailor materials → research contact + draft outreach into the approval queue. Implemented as `run_application_pipeline` (`services/pipeline.py`, scheduler-free and Lambda-portable) with `build_default_dependencies` as the composition root; `app/scheduler.py` is the thin APScheduler wrapper (`PIPELINE_HOUR`/`PIPELINE_MINUTE`, default 02:00; `python -m app.scheduler --once` for an immediate run). `run_job_safely` gives each job log-and-swallow isolation.

**Sending** (`services/outreach_send.py`): the only exit point. `approved → sent` through the state machine only; a draft without a researched contact email is skipped (never guessed); failures stay approved for retry; re-runs never re-send. Manual path: approve → `POST /outreach/{id}/send` (Send stamp on the folio). Auto path: `OUTREACH_AUTO_SEND=true` in the pipeline. `MailClient` = mock outbox by default; `GMAIL_ENABLED=true` selects the real Gmail client (`integrations/real/gmail.py`). Job source: `JOB_SOURCE_PROVIDER` picks mock (default) or the compliant Remotive API (`integrations/real/jobs_remotive.py`).

**Interview scan (separate):** scoped, query-filtered Gmail read for interview invites only → create/update `interviews` → generate prep packet. Implemented as `run_interview_scan` (`services/interview_scan.py`): `INTERVIEW_INBOX_SCAN=false` disables all reads, `INTERVIEW_SCAN_QUERY` scopes them, the conservative detector drops non-invites unstored. Interviews dedupe on `(user_id, source_message_id)`; `interviews.stage` (detected → scheduled → completed, or cancelled) validates transitions. Prep packets: heuristic default grounded in the linked application's materials + CV claims; `INTERVIEW_LLM_PREP=true` selects the DEEP-tier generator (`llm/prep.py`). Second cron trigger at `INTERVIEW_SCAN_HOUR` (03:00); `python -m app.scheduler --once --job interviews` for an immediate run. Surfaced at `/interviews` (API + dashboard section/folio).

A failure in one job never blocks the other (`run_job_safely` — observed live).

## Guardrails

- Secrets: env only. `.env` git-ignored, `.env.example` committed. OAuth tokens encrypted at rest.
- Data minimization: **no personal text/email content mining.** Master CV sources = Drive, GitHub, uploads only. Inbox reads are scoped to interview detection and gated by a flag.
- Truthfulness: tailored materials and outreach derive from real Master CV data. Never invent experience or contacts.
- External-action safety: confirm before real sends, first-time paid calls, deletions.

## Config flags (defaults reflect the safe path)

| Env var | Default | Effect |
|---|---|---|
| `ENABLE_LINKEDIN_SOURCE` | `false` | LinkedIn scraping (violates their ToS; they block aggressively). Use the compliant source instead. |
| `OUTREACH_AUTO_SEND` | `false` | When false, drafts wait in the approval queue (send via `POST /outreach/{id}/send` after approving). When true, the nightly pipeline auto-approves fresh drafts and sends those with a researched contact email. |
| `GMAIL_ENABLED` | `false` | When false, the in-process mock outbox is used — nothing can leave the machine. True selects the real Gmail client (stored Google credential must carry the gmail.send scope). |
| `JOB_SOURCE_PROVIDER` | `mock` | `mock` = fixture-backed job source; `remotive` = the compliant public Remotive API. |
| `INTERVIEW_INBOX_SCAN` | `true` | Set false to disable all inbox reads (the scanner is never invoked). |
| `INTERVIEW_LLM_PREP` | `false` | When false, prep packets use the deterministic template generator. True selects the LLM-backed one (needs `LLM_ENABLED`; else it warns and stays heuristic). |
| `SHORTLIST_SIZE` | `250` | Stage-1 shortlist size. |
| `TOP_N` | `10` | Deep-ranked final matches. |
| `ANTHROPIC_MODEL_BULK` | (sonnet 5 high) | Bulk scoring/extraction. |
| `ANTHROPIC_MODEL_DEEP` | (opus 4.8 high) | Deep ranking/drafting. |
| `LLM_ENABLED` | `false` | When false, the deterministic fake LLM client is used (no API key). True selects the real Anthropic client. |
| `MASTER_CV_LLM_STRUCTURING` | `false` | When false, ingestion uses the heuristic PAR structurer. True selects the LLM-backed one (needs `LLM_ENABLED`; else it warns and stays heuristic). |
| `MATCHING_LLM_RANKING` | `false` | When false, matching uses the heuristic scorer/reranker. True selects the LLM-backed stages (needs `LLM_ENABLED`; else it warns and stays heuristic). |
| `TAILORING_LLM_DRAFTING` | `false` | When false, tailoring + outreach use the deterministic template drafters. True selects the LLM-backed ones (needs `LLM_ENABLED`; else it warns and stays heuristic). |
| `GDRIVE_MCP_ENABLED` | `false` | When false, the fixture-backed mock Drive client is used (no OAuth/MCP). True selects the MCP-backed client. |
| `UPLOADS_DIR` | (empty) | Local folder of user-supplied career artifacts (text/markdown). Empty disables uploads ingestion. |
| `GDRIVE_SOURCE_FOLDER_ID` | (empty) | Approved career-docs folder. With no folder and no broad scan, nothing is ingested. |
| `GDRIVE_ALLOW_BROAD_SCAN` | `false` | Never scan the whole Drive by default. True is required to look outside the approved folder. |

## Out of scope (do not build)

- Mining personal texts/emails for skills.
- RL / self-updating policy layer (v2). Log outcome data now; don't learn from it yet.
- Auto-sending outreach without approval by default.

## Dependencies (justify additions here)

- `fastapi`, `uvicorn` — API + server.
- `sqlalchemy`, `alembic`, `psycopg` — DB + migrations.
- `pydantic-settings` — typed config.
- `apscheduler` — nightly triggers.
- `anthropic` — LLM layer.
- `mcp` — MCP client SDK for the GitHub + Google Drive server sessions.
- `pytest`, `ruff`, `mypy` — quality.

## Milestone tracker (update as you go)

- [x] M0 — scaffold, DB, interfaces + mocks, fixtures (scheduler moved to M5)
- [x] M1 — Master CV (mocked sources)
- [x] M2 — jobs + two-stage matching (mocked)
- [x] M3 — tailoring + outreach drafting → approval queue
- [x] M4 — dashboard
- [x] M5 — orchestration (idempotent)
- [x] M6 — real integrations behind flags (live-verified: `python -m app.tools.verify_mcp`)
- [x] M7 — interview scan + prep packets
