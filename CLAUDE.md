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

## LLM layer

All Anthropic Messages API calls go through `app/llm/`. Prompt for **strict JSON** on structured steps; strip fences, validate against a schema, retry once on parse failure. Central retries, timeouts, and token/cost logging live here.

Tiered models, env-configurable, never hardcoded:
- `ANTHROPIC_MODEL_BULK` — stage-1 scoring + bulk extraction (cheap/fast).
- `ANTHROPIC_MODEL_DEEP` — top-10 re-rank, outreach drafting, prep packets (strong).

## MCP integrations (GitHub + Google Drive)

GitHub and Google Drive are accessed through their **MCP servers**, not hand-rolled REST clients. The `real/` implementations of `GitHubClient` and `DriveClient` open an MCP client session (code in `integrations/mcp/`) and call the server's tools to list/read repos, READMEs, contribution signals, and Drive docs/PDFs; extracted content is then handed to `llm/` for PAR structuring. The same tools can also be surfaced directly to the agent for exploratory extraction — either way the boundary is the same.

- Interfaces and mocks are **unchanged**: `MockGitHubClient` / `MockDriveClient` still front the pipeline, so it runs offline on fixtures and `domain/` stays decoupled from MCP.
- Configure server endpoints/commands via `GITHUB_MCP_SERVER` and `GDRIVE_MCP_SERVER` in `.env`.
- OAuth still governs the underlying access; tokens are passed to the MCP servers and stored encrypted per the guardrails.
- Gmail (send + scoped interview scan), the compliant job source, and LinkedIn remain direct API clients — MCP is only for Drive + GitHub.

## Pipeline (two independent nightly jobs)

**Application pipeline:** refresh Master CV (if sources changed) → fetch jobs (24h) → score all → top `SHORTLIST_SIZE` (~250) → deep re-rank to `TOP_N` (10) with rationale → tailor materials → research contact + draft outreach into the approval queue.

**Interview scan (separate):** scoped, query-filtered Gmail read for interview invites only → create/update `interviews` → generate prep packet.

A failure in one job never blocks the other.

## Guardrails

- Secrets: env only. `.env` git-ignored, `.env.example` committed. OAuth tokens encrypted at rest.
- Data minimization: **no personal text/email content mining.** Master CV sources = Drive, GitHub, uploads only. Inbox reads are scoped to interview detection and gated by a flag.
- Truthfulness: tailored materials and outreach derive from real Master CV data. Never invent experience or contacts.
- External-action safety: confirm before real sends, first-time paid calls, deletions.

## Config flags (defaults reflect the safe path)

| Env var | Default | Effect |
|---|---|---|
| `ENABLE_LINKEDIN_SOURCE` | `false` | LinkedIn scraping (violates their ToS; they block aggressively). Use the compliant source instead. |
| `OUTREACH_AUTO_SEND` | `false` | When false, approved-quality drafts wait in the approval queue. When true, they auto-send. |
| `INTERVIEW_INBOX_SCAN` | `true` | Set false to disable all inbox reads. |
| `SHORTLIST_SIZE` | `250` | Stage-1 shortlist size. |
| `TOP_N` | `10` | Deep-ranked final matches. |
| `ANTHROPIC_MODEL_BULK` | (sonnet 5 high) | Bulk scoring/extraction. |
| `ANTHROPIC_MODEL_DEEP` | (opus 4.8 high) | Deep ranking/drafting. |

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

- [ ] M0 — scaffold, DB, interfaces + mocks, fixtures
- [ ] M1 — Master CV (mocked sources)
- [ ] M2 — jobs + two-stage matching (mocked)
- [ ] M3 — tailoring + outreach drafting → approval queue
- [ ] M4 — dashboard
- [ ] M5 — orchestration (idempotent)
- [ ] M6 — real integrations behind flags
- [ ] M7 — interview scan + prep packets
