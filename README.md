# JobPilot

JobPilot is a single-user, AI-assisted job-search operating system. It builds a canonical Master CV from the job seeker's own artifacts, finds fresh high-fit roles, ranks them against the Master CV, drafts tailored application materials, queues personalized outreach for approval, detects interview invitations, and generates interview prep packets.

The project is optimized for application quality and fit, not high-volume spraying. The goal is to turn job searching into a disciplined, repeatable pipeline where the user can inspect what the system found, why each role matched, what materials were generated, and what action is waiting for approval.

## Core idea

Employers increasingly use automation across sourcing, screening, job descriptions, ATS filters, and interview workflows. JobPilot is a counterweight for the individual job seeker: it uses the user's own work history, projects, GitHub activity, and Google Drive documents to construct a structured source of truth, then uses that source of truth to evaluate and prepare applications.

The system deliberately avoids mining personal texts or general email content for soft-skill inference. In v1, the Master CV is built only from Google Drive, GitHub, and explicit user uploads. Gmail access is scoped to two narrow cases: sending approved outreach drafts and detecting interview-related messages when the inbox scan flag is enabled.

## What JobPilot does

- Builds a versioned Master CV from Google Drive, GitHub, and uploaded artifacts.
- Converts experience and project evidence into PAR-framed claims: Problem, Action, Result.
- Fetches recent jobs from a pluggable job-source interface.
- Scores every fetched role against the Master CV.
- Deep-reranks the strongest matches into a final top-N shortlist.
- Generates tailored resume and cover-letter snippets using only verified Master CV content.
- Researches companies and likely contacts for each top match.
- Drafts outreach into an approval queue instead of auto-sending by default.
- Detects interview invitations through a scoped Gmail query.
- Generates interview prep packets with role context, likely questions, interviewer notes, and materials to review.
- Surfaces everything in a lightweight dashboard.

## v1 scope

### In scope

- Master CV builder from:
  - Google Drive documents and PDFs
  - GitHub repositories, READMEs, languages, and contribution signals
  - explicit user uploads
- GitHub and Google Drive integration through MCP servers.
- Mock-first interfaces for every external dependency.
- Two-stage job matching:
  - stage 1: bulk scoring across all fetched roles
  - stage 2: deeper model analysis on the shortlist
- Tailored application materials derived strictly from the Master CV.
- Outreach draft generation with human approval before sending.
- Scoped interview-invite detection.
- Interview prep packet generation.
- Dashboard for Master CV, matches, applications, outreach, and interviews.
- Nightly orchestration.

### Out of scope

- Mining personal texts for skills or personality signals.
- Mining general email content for soft-skill inference.
- Auto-sending outreach without human approval by default.
- Reinforcement learning or self-updating policy logic.
- Treating LinkedIn scraping as the default job source.

## Architecture

```text
                   nightly orchestrator
                          │
      ┌───────────────────┼───────────────────┐
      │                   │                   │
 Google Drive MCP     GitHub MCP          Uploads
      │                   │                   │
      └──────────────┬────┴────┬──────────────┘
                     │         
              Master CV Builder
                     │
              versioned Master CV
                     │
        ┌────────────┴────────────┐
        │                         │
 Application pipeline        Interview scan
        │                         │
 fetch recent jobs           scoped Gmail query
        │                         │
 score all jobs              detect interview invite
        │                         │
 shortlist top matches       create/update interview
        │                         │
 deep rerank top-N           generate prep packet
        │
 tailor materials
        │
 draft outreach
        │
 approval queue
        │
       Postgres ── FastAPI ── Next.js dashboard
```

Two scheduled jobs run independently:

1. **Application pipeline**: refresh Master CV if sources changed, fetch recent jobs, score and rank matches, tailor materials, research contacts, and draft outreach.
2. **Interview scan**: run a scoped Gmail query for interview invitations, update interview records, and generate prep packets.

A failure in one pipeline should not block the other.

## MCP integration strategy

GitHub and Google Drive are accessed through MCP servers rather than hand-rolled REST clients inside the business logic.

The application still uses stable internal interfaces:

- `GitHubClient`
- `DriveClient`
- `JobSource`
- `MailClient`
- `ResearchClient`

The real GitHub and Drive clients open MCP sessions behind those interfaces. The rest of the application does not need to know whether the data came from a mock fixture, a local upload, or a real MCP-backed integration.

This gives the project three important properties:

1. **Mock-first development**: the full pipeline can run offline on fixtures before any real credential exists.
2. **Replaceable integrations**: GitHub/Drive implementation details stay behind interfaces.
3. **Testable domain logic**: ranking, state transitions, CV structuring, and idempotency can be tested without live APIs.

## Repo layout

```text
app/
  main.py              # FastAPI entrypoint
  config.py            # typed env-driven settings
  db/                  # SQLAlchemy models, sessions, Alembic env
  domain/              # pure business logic
  services/            # orchestration-level application services
  integrations/
    base.py            # integration interfaces
    mock/              # fake clients and fixtures
    real/              # real Gmail, job source, flagged LinkedIn adapter
    mcp/               # MCP client sessions for GitHub and Google Drive
  llm/                 # Anthropic calls, JSON parsing, retries, cost logging
  scheduler.py         # APScheduler triggers

tests/
  fixtures/            # seed CVs, jobs, GitHub/Drive examples, interview invites

web/                   # Next.js + Tailwind dashboard

PLAN.md
CLAUDE.md
.env.example
README.md
```

## Data model

Postgres is the transactional source of truth.

Core tables:

- `users`
- `oauth_credentials`
- `cv_sources`
- `master_cv`
- `jobs`
- `job_matches`
- `applications`
- `outreach`
- `interviews`
- `prep_packets`

Important modeling rules:

- `master_cv.content_json` is versioned and structured.
- Every resume claim should trace back to source evidence.
- Experiences and projects should use PAR framing.
- Jobs are deduped by `(source, external_id)`.
- Nightly runs must be idempotent.
- Application, outreach, and interview stages should be explicit state machines with validated transitions.

## LLM layer

All Anthropic calls go through a single `app/llm/` module.

Responsibilities:

- central model configuration
- strict JSON prompting for structured tasks
- schema validation
- fence stripping
- one retry on parse failure
- timeouts and retry policy
- token and cost logging

Model tiers are configured by environment variables:

- `ANTHROPIC_MODEL_BULK`: bulk extraction and first-pass scoring
- `ANTHROPIC_MODEL_DEEP`: top-N reranking, outreach drafting, and prep packets

No model string should be hardcoded outside configuration.

## Tech stack

Backend:

- Python 3.12
- `uv`
- FastAPI
- SQLAlchemy 2.x
- Alembic
- Postgres
- APScheduler
- Anthropic Messages API
- MCP client SDK

Frontend:

- Next.js
- React
- Tailwind CSS

Quality:

- pytest
- ruff
- mypy

## Local setup

```bash
uv sync
cp .env.example .env
alembic upgrade head
```

Run the API:

```bash
uv run uvicorn app.main:app --reload
```

Run the scheduler locally:

```bash
uv run python -m app.scheduler
```

Run the dashboard:

```bash
cd web
npm run dev
```

Run quality checks:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy app
uv run pytest -q
```

The fixture-backed pipeline should pass with zero real credentials. If real credentials are required to run tests, that is a bug.

## Environment variables

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/jobpilot
SECRET_KEY=replace-me
TOKEN_ENCRYPTION_KEY=replace-me

ANTHROPIC_API_KEY=replace-me
ANTHROPIC_MODEL_BULK=replace-me
ANTHROPIC_MODEL_DEEP=replace-me

GITHUB_MCP_SERVER=replace-me
GDRIVE_MCP_SERVER=replace-me

ENABLE_LINKEDIN_SOURCE=false
OUTREACH_AUTO_SEND=false
INTERVIEW_INBOX_SCAN=true
SHORTLIST_SIZE=250
TOP_N=10
```

## Safety and privacy guardrails

- Do not commit secrets.
- Keep `.env` out of git.
- Store OAuth tokens encrypted at rest.
- Do not mine personal texts.
- Do not mine general email content for inferred traits or skills.
- Use Gmail reads only for scoped interview detection.
- Draft outreach by default; do not send unless the user approves.
- Do not fabricate experience, metrics, contacts, or credentials.
- Prefer compliant job-source APIs. LinkedIn scraping remains disabled by default and explicitly flagged.
- Ask before irreversible or external actions such as sending real emails, deleting files, or triggering paid API calls for the first time.

## Milestones

### M0 — Scaffold

- Create repo structure.
- Configure `uv`, FastAPI, SQLAlchemy, Alembic, pytest, ruff, and mypy.
- Add `.env.example`.
- Define integration interfaces.
- Add mock clients and fixtures.
- Confirm offline tests run without credentials.

### M1 — Master CV

- Ingest fixture Drive, GitHub, and upload sources.
- Extract structured evidence.
- Generate a versioned Master CV.
- Store provenance in `cv_sources`.
- Test PAR structuring and deduplication.

### M2 — Jobs and matching

- Implement `JobSource` interface.
- Add fixture-backed job ingestion.
- Score all jobs against the Master CV.
- Shortlist top matches.
- Deep-rerank final top-N matches.
- Test ranking and dedupe behavior.

### M3 — Tailoring and outreach

- Generate tailored resume and cover snippets.
- Research company/contact context through `ResearchClient`.
- Draft outreach into an approval queue.
- Ensure no generated claim lacks Master CV support.

### M4 — Dashboard

- Add dashboard overview.
- Show Master CV version/link.
- Show application pipeline status.
- Show outreach approval queue.
- Show interview records and prep-packet links.

### M5 — Orchestration

- Wire nightly application pipeline through APScheduler.
- Ensure reruns are idempotent.
- Ensure application and interview jobs fail independently.

### M6 — Real integrations

- Add MCP-backed GitHub client.
- Add MCP-backed Google Drive client.
- Add Gmail send behind approval.
- Add compliant job-source adapter.
- Keep mocks as the default for tests.

### M7 — Interview scan and prep packets

- Add scoped Gmail query for interview invitations.
- Create/update interview records.
- Generate interview prep packets.
- Surface prep packets on the dashboard.

## Definition of done

The v1 system is complete when the full pipeline can run on fixtures with zero real credentials and produce:

- a versioned Master CV
- ranked top-N job matches with rationales
- tailored application materials
- outreach drafts waiting in the approval queue
- an interview record from a fixture invite
- a generated prep packet
- dashboard views for the full workflow

Real integrations should drop in behind feature flags and interfaces without changing the domain logic.
