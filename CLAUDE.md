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
  render/              # frozen V2 docx renderer (render_master_cv.py — integrate, never rewrite)
  scheduler.py         # APScheduler triggers -> service functions
templates/             # resume_template.docx — docxtpl template cloned from the real CV (frozen)
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

`users`, `oauth_credentials` (tokens encrypted at rest), `cv_sources` (provenance), `master_cv` (versioned `content_json`, all PAR-framed), `jobs`, `job_matches`, `applications`, `outreach`, `interviews`, `prep_packets` — plus the V2 claims layer: `experiences` (user-assigned `section` + `sort_order`), `evidence` (one citable chunk per commit/README/Drive doc, deduped on `(user_id, source_type, source_ref)`; source types documented, deliberately no CHECK constraint), `claims` (strict PAR: `status`, `problem_cost_dimension`, `problem_inefficiency`, `result_kind`, `result_status`, `result_metric_json` with `resolves`, `validation_flags`), and `claim_evidence` (`field` per link; `outcome_quote` required when `field='result'`).

- `applications.status`: `drafted | applied | interviewing | rejected | offer | ignored`
- `interviews.stage` (`detected → confirmed → scheduled → completed`, or `cancelled`) and `outreach.status`: explicit state machines with validated transitions in `domain/`. `validation_runs` (V2) logs every PAR-validator run and interview provenance verification.
- Dedupe jobs on `(source, external_id)`. Nightly jobs are **idempotent** — re-running never double-applies, double-sends, or duplicates rows.
- **Master CV persistence (V2-only)**: a Master CV version is exclusively an approved-claims snapshot written through `MasterCvSnapshotStore` (`db/master_cv_snapshot_store.py`), fingerprint-idempotent, distinguished by `content_json.snapshot_of == "approved_claims"`. The store's reads skip any legacy V1 builder-generated rows still in the table; new versions allocate above them. The V1 write path (`refresh_master_cv`) is deleted — `services/master_cv_ingestion.py` retains only pure evidence readers (`ingest_*_sources`, `build_master_cv`, no persistence), and the legacy `MasterCvRepository` implementations remain solely as historical-row readers awaiting Phase-3 deletion (see `docs/V2_AUDIT.md`). `cv_sources` dedupe on `(user_id, source_type, external_ref)`.
- **Uploads** (third evidence source alongside Drive + GitHub): `UploadsClient` interface with `LocalUploadsClient` (`integrations/uploads.py`) over a configured `UPLOADS_DIR` — empty by default, meaning disabled. Text/markdown allowlist (`apply_upload_policy`) filters before any read; ingestion records `CvSource(source_type="upload")` and `build_master_cv` merges all three source types.
- **Jobs + matching**: `JobSource` (mock + future real) feeds `jobs` (deduped on `(source, external_id)`). Two-stage matching (`domain/matching.py`, pure + deterministic) scores every job (stage 1) → shortlists `SHORTLIST_SIZE` → deep re-ranks `TOP_N` with a rationale (stage 2), both behind `JobScorer`/`JobReranker` protocols with heuristic (default) and LLM-backed (`llm/matching.py`, flag `MATCHING_LLM_RANKING`) implementations. `run_matching` (`services/matching.py`) persists results to `job_matches` per `(user_id, master_cv_version)` with replace semantics.
- **Project roster (V2.1, M13)**: the audit's root-cause fix — an experience is a real-world entity, not a file. `experiences` gains `kind` (`employer_role|project`), `status` (`proposed → confirmed | discarded`; `merged` terminal via `merge_experiences`, which moves claims + evidence and aliases the source's name), `aliases`, `merged_into_id` (migration `0010`); `evidence` gains nullable `experience_id` (the chunk's project assignment). Flow: `run_roster_detection` (`services/roster.py`) proposes entities over normalized sources (`RosterProposer` — heuristic per-source default, LLM DEEP-tier behind `ROSTER_LLM_DETECTION`) → **the human confirms/renames/merges/discards on the dashboard's roster section** (`api/roster.py`: `POST /roster/detect`, `GET /roster`, `confirm|discard|merge`, `PATCH`) → `run_roster_assignment` chunks every source (`domain/chunking.py`, paragraph chunks ≤1200 chars with char spans encoded as `ref#chars=start-end`; `split_span_ref`) and assigns each chunk to a confirmed entity (`ChunkAssigner` — alias-overlap heuristic refuses ties; LLM BULK; repo evidence assigns by repo-ref alias directly; unmatched chunks stay honestly unassigned and never feed extraction). Detection is decision-preserving: proposals dedupe by name+alias, never downgrade confirmed, never resurrect discarded. Tools: `python -m app.tools.run_roster_detection`; the extraction tool refreshes assignments first.
- **Claims (V2)**: the claim state machine is `extracted → pending_review → approved | rejected` (rejection requires a reason; decisions are terminal and retained — they are V3 golden-set data). Extraction (`services/claim_extraction.py`) normalizes source text first (`domain/text_normalization.py` — PDF reflow, dehyphenation, word-per-line repair), is two-pass per experience group (pass 1 work statements → Actions, pass 2 outcome statements → Results), always lands claims as `pending_review`, and bounces FIXABLE PAR-validator failures (`domain/par_validation.py`) to re-extraction once — the problem-absence family (`problem_missing`/`problem_not_pain_point`) never bounces: evidence that states no pain point can't be re-prompted into stating one, and bouncing on it doubled LLM cost on every commit-heavy group. **Cost controls:** oversized groups split into ≤30-chunk/≤14K-char batches per two-pass call (bounds output too — pass-1 JSON quotes the input); the chunk block rides in the cached system prefix so pass 2 (and a bounce) reads pass 1's prompt cache at ~10% input price; a group whose evidence fingerprint (`experiences.extraction_hash`, migration `0011`) matches its last successful extraction is skipped entirely (`force=True` or naming it in `experience_names` overrides). **Structural failures are dropped, never queued** (`STRUCTURAL_CODES`: `problem_not_specific` — one-word/vague problems, action restatements, resume-artifact headers; `action_fragment` — short or mid-clause-truncated actions); advisory failures queue flagged; drops are logged to `validation_runs`. The validator encodes the non-negotiables: Problem must declare a cost dimension and/or inefficiency; Action must name tools; Results are content-gated (quantified → verbatim metric in cited chunk; qualitative → verbatim `outcome_quote`; missing → never filled) and must resolve a declared pain point (`resolves` coupling, flagged "result does not address stated problem"). Cross-experience dedupe: content already queued or decided anywhere for the user never queues again. **Roster mode** (when confirmed entities have assigned evidence): one extraction group per confirmed entity, its chunks only — a claim citing evidence outside its group is structurally dropped (`result_project_mismatch`/`evidence_outside_project`), and one outcome span supports at most one claim's Result (later claims keep the work, lose the Result, flagged `duplicate_outcome_span`); without a confirmed roster, legacy per-file grouping runs with a loud warning. Extractors sit behind `ClaimExtractor` — heuristic default (line- then sentence-split statements), LLM two-pass (`llm/extraction.py`, flag `CLAIMS_LLM_EXTRACTION`, BULK tier, grounding-filtered); an LLM failure raises `ClaimExtractionError` — the group is skipped loudly (logged + `validation_runs` kind `extraction_failure`), **never** silently answered by the heuristic. **Flagged claims cannot be approved as-is** (`FlaggedClaimApprovalError` → HTTP 409): edit-attest (clears flags with provenance) or reject. Repos: `InMemoryClaimRepository` + `SqlClaimRepository` (migration `0006`); re-runs replace unreviewed claims only and never re-queue content a human decided on.
- **Master CV rendering (V2)**: the docx is a rendered view of one approved-claims version. `render_master_cv_artifact` (`services/master_cv_render.py`) snapshots first (idempotent), builds the renderer's exact JSON contract (`domain/resume_context.py` — approved claims only, section routing, `sort_order`, `skill_list` not `items`, `None`→`""`), writes context + docx under `ARTIFACTS_DIR`, and runs the **frozen** renderer (`app/render/render_master_cv.py` + `templates/resume_template.docx` — integrated as-is, ruff-excluded, never rewritten; its fidelity assertions run on every render). Header/education/skills come from the user's profile JSON (`RESUME_PROFILE_PATH`) — never invented; unconfigured rendering is a 503. Artifact rows (`artifacts` table, migration `0008`, kind `master_cv_docx`) upsert per `(user, kind, version)`; `POST /master-cv/render` + `GET /master-cv/download`.
- **Claim review layer (V2)**: `services/claim_review.py` + `api/claims.py` — `GET /claims/queue` (`?missing_results=true` is the Missing-Results queue, a filtered view of the same layer), `POST /claims/{id}/approve|reject|edit-approve`, `GET /experiences`, `POST /experiences/{id}/layout` (section picker + sort_order), `POST /master-cv/snapshots` + `GET /master-cv/snapshots/latest`. Edit-then-approve computes a pure edit plan (`plan_claim_edit`): every edited field group gets `user_attestation` provenance — an evidence row holding the attested text, linked on that field — and an edited/supplied Result becomes `result_status=user_attested` (how Missing-Results resolves); edits clear `validation_flags` (the human supersedes the validator). A **Master CV version is a snapshot of approved claims only** (`domain/master_cv_snapshot.py`, stores in `services/master_cv_snapshot.py` + `db/master_cv_snapshot_store.py` over the `master_cv` table): enforced at the version-snapshot query (`status=approved`) and re-filtered in the builder; content groups experiences by `section` in `sort_order` for the M11 renderer; versioning is fingerprint-idempotent.
- **Dashboard**: `web/` (Next.js app router + Tailwind v4, TypeScript) renders one "pipeline ledger" page — V2 order: claims review queue (P/A/R review cards with cited evidence quotes + click-through source links, approve / reject-with-reason / attest-missing-result; the Missing-Results queue is a toggle on the same section), Master CV docx render + download, outreach approval queue, interview confirm queue (confirm goes through `POST /interviews/{id}/confirm` so the prep packet is generated), matches, applications — plus detail views. Job/application cards link the posting (`canonical_url` preferred, original `url` fallback). It talks to `app/api/dashboard.py` + `app/api/claims.py` + `app/api/interviews.py` with repos injected in tests and lazily built over SQL in prod. CORS is restricted to `DASHBOARD_ORIGINS` (default `http://localhost:3000`); the web app reads `NEXT_PUBLIC_API_URL`/`NEXT_PUBLIC_USER_ID` (see `web/.env.example`).
- **Job posting links (V2)**: `jobs.url` stores the posting exactly as the source gave it; `jobs.canonical_url` (migration `0009`) is the deterministic tracking-stripped canonical form (`canonical_job_url` in `domain/jobs.py`), stamped at ingestion. Evidence click-through: `evidence_source_url` maps provenance to a human-readable URL (commit evidence `source_ref` is `owner/repo@sha`).
- **Tailoring + outreach (approval queue)**: state machines live in `domain/applications.py` — `applications.status` (`drafted → applied → interviewing → rejected|offer`, `drafted → ignored`) and `outreach.status` (`drafted → approved → sent`, `drafted → discarded`), transitions validated everywhere (`InvalidTransitionError`). Tailoring (`domain/tailoring.py`) and drafting (`domain/outreach.py`) sit behind `MaterialsTailorer`/`OutreachDrafter` protocols — heuristic defaults render Master CV claims verbatim; LLM-backed versions (`llm/drafting.py`, flag `TAILORING_LLM_DRAFTING`, DEEP tier) ground highlights by claim id (hallucinated ids dropped) and fall back to the heuristics on failure. Contacts come from the `ResearchClient` interface (mock fixture-backed) or are honestly absent — never invented. `ApplicationRepository` (in-memory + SQL, migration `0004`) keeps one application per `(user_id, job)` and one outreach row per application; re-runs refresh *drafted* rows only and never overwrite a human decision. `run_drafting` (`services/outreach.py`) orchestrates; the approval queue is served by `GET /outreach/queue` + `POST /outreach/{id}/approve|discard` (`api/outreach.py`).

## LLM layer

All Anthropic Messages API calls go through `app/llm/`. Prompt for **strict JSON** on structured steps; strip fences, validate against a schema, retry once on parse failure. Central retries, timeouts, and token/cost logging live here. `ANTHROPIC_TIMEOUT_SECONDS` defaults to 300 — extraction calls emit thousands of output tokens and a short timeout makes the SDK retry (and re-bill) work the server already did. Shared expensive context goes in `cached_context` (prompt-cached system prefix); keep the prefix identical across calls that should share the cache.

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
repositories, their READMEs, commit messages, and contribution signals. The `GitHubClient`
interface (`list_candidate_repos`, `read_repo`, `list_commits`, `get_repo_metadata`,
`list_changed_repos`) exposes no issue/PR/write operations. `list_commits` (V2) feeds claim
extraction — commit messages are legal Action *and* Result evidence under the content gate.

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

**Application pipeline (V2-only):** snapshot approved claims as the Master CV version (idempotent) and adapt it via `master_cv_from_snapshot` → fetch jobs (24h) → score all → top `SHORTLIST_SIZE` (~250) → deep re-rank to `TOP_N` (10) with rationale → tailor materials → research contact + draft outreach into the approval queue. **With zero approved claims the run stops before matching — nothing is ever generated from an unreviewed ledger.** The retired V1 refresh (build-from-raw-evidence, no review) is never invoked; matching, tailoring, outreach, prep packets, and the dashboard all consume the approved-claims snapshot. Implemented as `run_application_pipeline` (`services/pipeline.py`, scheduler-free and Lambda-portable) with `build_default_dependencies` as the composition root; `app/scheduler.py` is the thin APScheduler wrapper (`PIPELINE_HOUR`/`PIPELINE_MINUTE`, default 02:00; `python -m app.scheduler --once` for an immediate run). `run_job_safely` gives each job log-and-swallow isolation.

**Sending** (`services/outreach_send.py`): the only exit point. `approved → sent` through the state machine only; a draft without a researched contact email is skipped (never guessed); failures stay approved for retry; re-runs never re-send. Manual path: approve → `POST /outreach/{id}/send` (Send stamp on the folio). Auto path: `OUTREACH_AUTO_SEND=true` in the pipeline. `MailClient` = mock outbox by default; `GMAIL_ENABLED=true` selects the real Gmail client (`integrations/real/gmail.py`). Job source: `JOB_SOURCE_PROVIDER` picks mock (default) or the compliant Remotive API (`integrations/real/jobs_remotive.py`).

**Interview scan (separate):** scoped, query-filtered Gmail read for interview invites only → detect → **verify provenance** → confirm queue. Implemented as `run_interview_scan` (`services/interview_scan.py`): `INTERVIEW_INBOX_SCAN=false` disables all reads, `INTERVIEW_SCAN_QUERY` scopes them, the conservative detector drops non-invites unstored and requires a verbatim **body** quote (`evidence_quote`). V2 hard provenance (migration `0007`): before any insert the message is re-fetched by id (`InboxScanner.get_message`) and the quote asserted a substring of the real body (`verify_invite_provenance`) — a missing id, unfetchable message, or mismatched quote is rejected and logged, never stored. Every check (pass or fail) lands in `validation_runs` (kind `interview_verification`; PAR validator runs log there too as `par_validation`). Interviews dedupe on `(user_id, gmail_message_id)`; `interviews.stage` is `detected → confirmed → scheduled → completed`, cancellable at any live stage — **no detected→scheduled shortcut**: verified detections wait in the confirm queue (`GET /interviews/queue`) until `POST /interviews/{id}/confirm`. **Prep packets are generated only for confirmed interviews** (the confirm action generates one; the scan back-fills confirmed-without-packet). Heuristic generator default; `INTERVIEW_LLM_PREP=true` selects the DEEP-tier one (`llm/prep.py`). Mode guard: the scheduler asserts at startup (`assert_mode_guard`) that `GMAIL_ENABLED=true` never runs against `MockMailClient`/`MockInboxScanner`. Second cron trigger at `INTERVIEW_SCAN_HOUR` (03:00); `python -m app.scheduler --once --job interviews` for an immediate run.

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
| `CLAIMS_LLM_EXTRACTION` | `false` | When false, claim extraction uses the deterministic heuristic two-pass extractor. True selects the LLM-backed one (needs `LLM_ENABLED`; else it warns and stays heuristic). The PAR validator gates every claim either way. |
| `ROSTER_LLM_DETECTION` | `false` | When false, roster detection/assignment use the deterministic heuristics (per-source proposals, alias-overlap assignment). True selects the LLM-backed ones (needs `LLM_ENABLED`; else it warns and stays heuristic). Either way a human confirms the roster before extraction. |
| `TAILORING_LLM_DRAFTING` | `false` | When false, tailoring + outreach use the deterministic template drafters. True selects the LLM-backed ones (needs `LLM_ENABLED`; else it warns and stays heuristic). |
| `GDRIVE_MCP_ENABLED` | `false` | When false, the fixture-backed mock Drive client is used (no OAuth/MCP). True selects the MCP-backed client. |
| `RESUME_PROFILE_PATH` | (empty) | The user's profile JSON (name/tagline/contact/education/skills) for rendering. Empty means rendering fails loudly (503) — header data is never invented. |
| `RESUME_TEMPLATE_PATH` | `templates/resume_template.docx` | The frozen docxtpl template cloned from the real CV. |
| `ARTIFACTS_DIR` | `var/artifacts` | Where rendered context JSON + docx files are written (`artifacts` rows point here). |
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
- `docxtpl` — renders the Master CV docx from the frozen template (the V2 renderer's engine; brings python-docx + jinja2).
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
- [x] M8 — V2 schema + two-pass extraction + PAR validator
- [x] M9 — review layer: approve/edit-attest/reject, section picker, approved-only snapshots
- [x] M10 — interview validation: verified provenance, confirm queue, mode guard, validation_runs
- [x] M11 — renderer integration: frozen renderer wired to approved claims, artifacts + download
- [x] M12 — dashboard: claims review queue, missing-results, confirm queue, docx download, posting links
- [x] M12.5 — audit Phase 1 anti-slop: normalization, structural gates, dedupe, loud failures
- [ ] M13 — audit Phase 2 project roster: detect → human confirm → chunk assignment → extraction per confirmed entity (built; awaiting review)
