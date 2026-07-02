# PLAN.md

Living plan for JobPilot. Keep scope, milestones, and open questions current.

## Scope (v1)

A single-user, mock-first job-search pipeline: build a versioned, PAR-framed Master CV
from the user's own artifacts (Google Drive, GitHub, uploads), match fresh roles, tailor
materials, draft outreach for approval, detect interview invites, and generate prep
packets. Optimize for **fit and quality per application, not volume.**

Non-negotiables:

- Every external dependency sits behind an interface with a fake + fixtures. The full
  pipeline runs offline with **zero real credentials** before any real API is wired.
- `domain/` stays pure — no imports from `integrations/real`, `integrations/mcp`, or `llm/`.
- No mining of personal texts or general email content.

## Architecture (Drive slice — implemented)

```
DriveClient (interface, app/integrations/base.py)
   ├── MockDriveClient   (app/integrations/mock/drive.py)  ← default, fixture-backed
   └── McpDriveClient    (app/integrations/mcp/drive.py)   ← real, MCP-backed (wiring pending)
                    │
        create_drive_client() factory  (app/integrations/drive_factory.py)
                    │
        Master CV ingestion service  (app/services/master_cv_ingestion.py)
          → apply_source_policy()   (app/services/source_policy.py)  ← MIME allowlist + folder scope
          → read + build CvSource provenance
                    │
        MasterCvBuilder  (app/domain/cv.py)  ← PAR structuring, pure domain
                    │
        MasterCv (versioned content_json + provenance)
```

The domain layer never sees a `DriveClient`; the service converts `DriveDocument` →
`CvSource` and hands provenance records to the builder.

## LLM layer (`app/llm/`) — implemented

The single boundary for Anthropic Messages API calls. Same mock-first shape as the rest:

```
LlmClient (protocol, app/llm/client.py)
   ├── FakeLlmClient   (app/llm/fake.py)     ← default; scriptable, offline, no API key
   └── AnthropicClient (app/llm/client.py)   ← real; SDK imported lazily
                    │
        create_llm_client()  (app/llm/factory.py)  ← fake unless LLM_ENABLED=true
```

- Callers pick a `ModelTier` (`BULK`/`DEEP`), never a model id; the concrete model,
  timeouts, and retries resolve from `app/config.py`.
- `complete_json()` (`app/llm/json_completion.py`) is the structured-step helper: strip
  ``` fences → `json.loads` → optional `validator` → **retry once** with a corrective
  nudge before raising `LlmJsonError`. Decodes at `temperature=0`.
- `CostTracker` (`app/llm/cost.py`) logs per-call tokens + estimated USD and accumulates
  totals; prices are configurable estimates keyed by model substring.
- `domain/` still imports nothing from here; LLM-backed `ClaimStructurer`/`JobScorer`/
  `JobReranker` implementations will live in `app/llm/` and inject through the existing
  protocols.

## Milestones

- [x] **M0 — Scaffold**: repo structure, `uv`/ruff/mypy/pytest, `.env.example`, interfaces + mocks, fixtures.
  - [x] Drive + GitHub interfaces + fixture-backed mocks + `tests/fixtures/`.
  - [x] `app/config.py`, `.env.example`.
  - [x] DB layer started: SQLAlchemy `Base` + `session` + first model (`oauth_credentials`).
  - [x] Alembic env + first migration (`0001` oauth_credentials); `alembic upgrade head`
        verified offline. `create_all` remains a dev/test convenience.
  - [x] FastAPI entrypoint (`app/main.py`, app factory + `/health`) with the OAuth
        router (`app/api/`). Lazy, injectable flow wiring; imports with zero creds.
  - [x] `web/` (landed with M4). `app/scheduler.py` moved to M5, where orchestration belongs.
- [x] **M1 — Master CV (mocked sources)**
  - [x] Mock Drive ingestion → PAR-framed Master CV with `cv_sources` provenance.
  - [x] Mock GitHub ingestion (README + contribution signals) into the same builder;
        `build_master_cv` merges Drive + GitHub evidence with per-source provenance.
  - [x] Source policy: Drive MIME allowlist + folder scope; GitHub owner scope +
        fork/private exclusion. Broad scan off by default for both.
  - [x] Persist `master_cv` (versioned `content_json`) + deduped `cv_sources`
        (migration `0002`). `MasterCvRepository` protocol with in-memory + SQL impls;
        `refresh_master_cv` builds and saves. Versioning is idempotent — a fingerprint
        that excludes volatile timestamps means an unchanged re-run adds no new version,
        and sources dedupe on `(user_id, source_type, external_ref)`.
  - [x] Uploads ingestion into the same builder: `UploadsClient` interface +
        `LocalUploadsClient` (`app/integrations/uploads.py`) over a configured
        `UPLOADS_DIR` (empty default = disabled; the one implementation is local disk, so
        it is real *and* offline — fixtures in `tests/fixtures/uploads/`). Format policy
        (`apply_upload_policy`, text/markdown allowlist) filters before any read;
        `ingest_upload_sources` records `CvSource(source_type="upload")` provenance and
        `build_master_cv` merges Drive + GitHub + uploads into one PAR-framed CV.
  - [x] LLM-backed PAR structurer (`LlmClaimStructurer`, `app/llm/claim_structurer.py`)
        behind the same `ClaimStructurer` interface — a drop-in for the heuristic. Uses the
        BULK tier via `complete_json`. Enforces **no fabrication** structurally: the model
        must return a verbatim `evidence_text` quote per claim, and any claim whose quote
        is not found (whitespace-normalized) in the source is dropped.
  - [x] Ingestion swap behind a flag: `create_cv_builder` (`app/services/cv_builder_factory.py`)
        picks heuristic vs. LLM structurer from `MASTER_CV_LLM_STRUCTURING`; `build_master_cv`
        / `build_master_cv_from_drive` default through it. Off by default; on-without-a-real-client
        logs and falls back to the heuristic rather than crashing.
- [x] **M2 — Jobs + two-stage matching (mocked)**
  - [x] `JobSource` interface + `MockJobSource` + `tests/fixtures/jobs/`.
  - [x] `jobs` + `job_matches` schema (migration `0003`); `JobRepository` protocol with
        in-memory + SQL impls. Jobs dedupe on `(source, external_id)`; matches stored per
        `(user_id, master_cv_version)` with replace semantics.
  - [x] Two-stage matching (`app/domain/matching.py`, pure): stage-1 bulk keyword scorer
        → shortlist (`SHORTLIST_SIZE`) → stage-2 deep re-rank (`TOP_N`) with rationale,
        both behind `JobScorer`/`JobReranker` protocols. `run_matching` orchestrates
        fetch → persist jobs → rank → persist matches; deterministic + idempotent.
  - [x] LLM-backed stages (`app/llm/matching.py`) behind the same `JobScorer`/`JobReranker`
        interfaces: `LlmJobScorer` (stage-1, BULK, one cheap call per job) and
        `LlmJobReranker` (stage-2, DEEP, one call over the shortlist, addressing jobs by a
        positional id so hallucinated/duplicate ids are dropped). Both degrade gracefully —
        a failed score → 0.0, a failed rerank → heuristic order — so a flaky call never sinks
        the nightly batch. `CvProfile` gained a PAR-rendered `summary` so the protocols stay
        unchanged across heuristic and LLM.
  - [x] Matching swap behind a flag: `create_matchers` (`app/services/matching_factory.py`)
        picks heuristic vs. LLM matchers from `MATCHING_LLM_RANKING`; `run_matching` defaults
        through it. Off by default; on-without-a-real-client logs and falls back.
- [x] **M3 — Tailoring + outreach drafting → approval queue**
  - [x] State machines in `domain/` (`app/domain/applications.py`): `applications.status`
        (`drafted → applied → interviewing → rejected|offer`, `drafted → ignored`) and
        `outreach.status` (`drafted → approved → sent`, `drafted → discarded`), both with
        validated transitions (`InvalidTransitionError`); terminal states cannot reopen —
        a discarded draft is never resurrected by a re-run.
  - [x] Tailoring (`app/domain/tailoring.py`, pure): `MaterialsTailorer` protocol →
        `TailoredMaterials` (summary, evidence highlights, cover letter). Heuristic
        default selects claims by keyword overlap with the job and renders them
        **verbatim** — materials derive only from real Master CV claims.
  - [x] Outreach drafting (`app/domain/outreach.py`, pure): `ResearchClient` interface
        (+ `MockResearchClient` over `tests/fixtures/research/contacts.json`) finds a
        contact or honestly returns none; `OutreachDrafter` protocol with a heuristic
        template default. Contacts are researched, never invented — no contact means a
        generic hiring-team greeting.
  - [x] LLM-backed drafters (`app/llm/drafting.py`, DEEP tier) behind
        `TAILORING_LLM_DRAFTING` via `create_drafters`
        (`app/services/drafting_factory.py`): the tailorer addresses claims by positional
        id so hallucinated highlight ids are dropped (invented evidence cannot appear);
        both degrade to the heuristics on any LLM failure.
  - [x] Persistence: `applications` + `outreach` tables (migration `0004`), one
        application per `(user_id, job)`, at most one outreach row per application.
        `ApplicationRepository` protocol with in-memory + SQL impls; **idempotent** —
        re-runs refresh *drafted* rows only and never overwrite a human decision.
  - [x] `run_drafting` (`app/services/outreach.py`): matches → tailor → application →
        research contact → draft → approval queue. Nothing sends; `OUTREACH_AUTO_SEND`
        only warns until the real mail client (M6).
  - [x] Approval-queue API (`app/api/outreach.py`): `GET /outreach/queue`,
        `POST /outreach/{id}/approve|discard`; illegal transitions → 409, missing → 404.
- [x] **M4 — Dashboard**
  - [x] Read API for the dashboard (`app/api/dashboard.py`): `GET /master-cv/latest`
        (claims + provenance summary), `GET /matches` (deep-ranked matches for the
        latest CV version), `GET /applications` (+ `/{id}` detail with the outreach
        draft), and `POST /applications/{id}/transition` through the domain state
        machine (404/409/422). Repos injected in tests, lazily built over SQL in prod
        (same pattern as the OAuth flow); CORS restricted to `DASHBOARD_ORIGINS`.
  - [x] `web/`: minimal Next.js (app router, TS) + Tailwind v4 dashboard — a
        single-sheet "pipeline ledger": approval queue first (approve/discard), ranked
        matches with rationale + matched terms, applications with state-machine-derived
        action stamps, evidence-sources card with the OAuth connect links (closing the
        M6 "connect UI" gap). One detail view per application (materials, cover letter,
        outreach draft with contact provenance). `NEXT_PUBLIC_API_URL` /
        `NEXT_PUBLIC_USER_ID` config; `npm run build` type-checks clean.
  - [x] Verified end-to-end offline: seeded SQLite via migrations + the mock pipeline,
        exercised every endpoint (incl. approve/discard/transition + CORS preflight)
        over real HTTP, dashboard + folio routes serve.
- [x] **M5 — Orchestration (idempotent nightly jobs)**
  - [x] `run_application_pipeline` (`app/services/pipeline.py`): refresh Master CV →
        match fresh jobs (`JOBS_SINCE_HOURS` window) → draft outreach into the approval
        queue, returning a `PipelineResult` summary. No scheduler import — the same
        function ports to EventBridge→Lambda untouched. Idempotent end to end (verified
        against SQLite over the real entrypoint: second run adds zero rows, preserves
        approved/applied decisions).
  - [x] `PipelineDependencies` + `build_default_dependencies`: the composition root —
        factory-selected clients (mock by default; MCP/real slot in via existing flags)
        over SQL repositories. New `create_job_source` / `create_research_client`
        factories (mock-only until M6).
  - [x] `app/scheduler.py` (moved from M0), thin: one APScheduler `CronTrigger`
        (`PIPELINE_HOUR`/`PIPELINE_MINUTE`, default 02:00) → the service function.
        `run_job_safely` logs-and-swallows so one broken job never takes down the
        scheduler or blocks another (the M7 interview scan registers alongside).
        `python -m app.scheduler --once` runs the pipeline immediately (exit code
        reflects success); no `--once` schedules it nightly. Dependencies build lazily
        at first fire, so the process starts with zero credentials.
- [ ] **M6 — Real integrations behind flags**
  - [x] **Encrypted OAuth credential store** — `oauth_credentials` model +
        `TokenCipher` (Fernet, `TOKEN_ENCRYPTION_KEY`); tokens encrypted at rest.
        `OAuthCredentialStore` protocol with `InMemoryOAuthCredentialStore` (mock-first)
        and `SqlOAuthCredentialStore` (SQLAlchemy, tested on SQLite). Factories accept a
        `store` + `user_id` and inject decrypted `DriveCredentials`/`GitHubCredentials`
        into the MCP clients.
  - [x] **OAuth authorization flow** — `OAuthProvider` interface
        (`app/integrations/oauth/`) with `MockOAuthProvider` (mock-first) and real
        `GoogleOAuthProvider` / `GitHubOAuthProvider` (httpx, tested offline via
        `MockTransport`). `OAuthFlowService` (`app/services/oauth_flow.py`) does
        start → callback (anti-CSRF `state`, single-use) → encrypted upsert, and
        `get_valid_credential` transparently refreshes near-expiry tokens.
  - [x] **OAuth HTTP routes** — `GET /oauth/{provider}/start` (302 → consent) and
        `/callback` (validate single-use state, exchange, encrypted upsert) on the
        FastAPI app (`app/api/oauth.py`), tested end-to-end with `TestClient` +
        mock providers. Misconfiguration (e.g. no `TOKEN_ENCRYPTION_KEY`) → HTTP 503.
        **Remaining:** a small UI to launch the connect flow (part of M4).
  - [~] **Google Drive MCP** — targets the Google Workspace MCP server
        (taylorwilsdon/google_workspace_mcp). Done: async `McpDriveClient` mapping the
        `DriveClient` interface onto `list_drive_items` / `search_drive_files` /
        `get_drive_file_content` / `get_drive_file_permissions`; config-driven endpoint;
        `list_tools` validation on connect; response→dataclass mapping, unit-tested
        offline via an injected fake session; credentials now sourced from the store.
        **Remaining before it talks to real Drive:**
        1. Confirm the server's auth contract (bearer header vs. server-side token).
        2. **Live tool-name verification** — `list_tools` will confirm/deny the four names
           against the actual deployment; override `DriveToolNames` if they differ.
        3. **Response format** — if the server returns human-formatted text (not
           structured content/JSON), add a parser at `_extract_payload`.
        4. **stdio transport** — only `http` is wired; stdio needs launch command/args
           config (`GDRIVE_MCP_COMMAND`/`GDRIVE_MCP_ARGS`).
  - [~] **GitHub MCP** — targets the official github/github-mcp-server. Done: async
        `McpGitHubClient` mapping the `GitHubClient` interface onto `search_repositories`
        / `get_file_contents` / `list_commits`; config-driven endpoint; `list_tools`
        validation; base64 README decode; response→dataclass mapping, unit-tested offline
        via an injected fake session; PAT now sourced from the store.
        **Remaining before it talks to real GitHub:**
        1. Confirm auth (bearer header for the remote server vs.
           `GITHUB_PERSONAL_ACCESS_TOKEN` for a locally launched one).
        2. **Languages breakdown** — no first-class per-repo languages tool; metadata
           reports the primary language only. Wire a `/languages` equivalent if exposed.
        3. **Response format / stdio** — same caveats as Drive (text-vs-structured
           payloads; only `http` transport wired).
  - [ ] Gmail send behind approval; compliant job-source adapter.
  - [ ] Mocks remain the default for tests.
- [ ] **M7 — Interview scan + prep packets**

## Decisions

- **Drive MCP server:** Google Workspace MCP (taylorwilsdon/google_workspace_mcp).
- **GitHub MCP server:** official github/github-mcp-server
  (`search_repositories` / `get_file_contents` / `list_commits`).
- **Async model:** `DriveClient`/`GitHubClient` are async; the ingestion service awaits
  them (mocks are async too).

## Open questions

- Does the chosen server return structured content/JSON or only human-formatted text?
  (Determines whether `_extract_payload` needs a text parser.)
- Auth handshake for the Workspace MCP server: bearer header vs. server-side token.
- Do we export Google Docs as text or Markdown for best PAR extraction fidelity?
- LLM-backed PAR structurer: **done** (`LlmClaimStructurer`) — verbatim-quote grounding is
  the no-fabrication guard. Still open: the parallel prompts/schemas for stage-1/stage-2
  matching, and validating extraction quality on real docs (grounding is enforced, but
  claim *usefulness* isn't measured yet).
- Verify the real per-model prices in `app/llm/cost.py` (currently public-price estimates).
