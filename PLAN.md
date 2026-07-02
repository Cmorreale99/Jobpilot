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

- [ ] **M0 — Scaffold**: repo structure, `uv`/ruff/mypy/pytest, `.env.example`, interfaces + mocks, fixtures.
  - [x] Drive + GitHub interfaces + fixture-backed mocks + `tests/fixtures/`.
  - [x] `app/config.py`, `.env.example`.
  - [x] DB layer started: SQLAlchemy `Base` + `session` + first model (`oauth_credentials`).
  - [x] Alembic env + first migration (`0001` oauth_credentials); `alembic upgrade head`
        verified offline. `create_all` remains a dev/test convenience.
  - [x] FastAPI entrypoint (`app/main.py`, app factory + `/health`) with the OAuth
        router (`app/api/`). Lazy, injectable flow wiring; imports with zero creds.
  - [ ] `app/scheduler.py`, `web/`.
- [ ] **M1 — Master CV (mocked sources)**
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
  - [ ] Uploads ingestion into the same builder.
  - [x] LLM-backed PAR structurer (`LlmClaimStructurer`, `app/llm/claim_structurer.py`)
        behind the same `ClaimStructurer` interface — a drop-in for the heuristic. Uses the
        BULK tier via `complete_json`. Enforces **no fabrication** structurally: the model
        must return a verbatim `evidence_text` quote per claim, and any claim whose quote
        is not found (whitespace-normalized) in the source is dropped.
  - [x] Ingestion swap behind a flag: `create_cv_builder` (`app/services/cv_builder_factory.py`)
        picks heuristic vs. LLM structurer from `MASTER_CV_LLM_STRUCTURING`; `build_master_cv`
        / `build_master_cv_from_drive` default through it. Off by default; on-without-a-real-client
        logs and falls back to the heuristic rather than crashing.
- [~] **M2 — Jobs + two-stage matching (mocked)**
  - [x] `JobSource` interface + `MockJobSource` + `tests/fixtures/jobs/`.
  - [x] `jobs` + `job_matches` schema (migration `0003`); `JobRepository` protocol with
        in-memory + SQL impls. Jobs dedupe on `(source, external_id)`; matches stored per
        `(user_id, master_cv_version)` with replace semantics.
  - [x] Two-stage matching (`app/domain/matching.py`, pure): stage-1 bulk keyword scorer
        → shortlist (`SHORTLIST_SIZE`) → stage-2 deep re-rank (`TOP_N`) with rationale,
        both behind `JobScorer`/`JobReranker` protocols. `run_matching` orchestrates
        fetch → persist jobs → rank → persist matches; deterministic + idempotent.
  - [ ] Swap the heuristic scorer/reranker for the LLM-backed stages behind the same
        interfaces (bulk model for stage 1, deep model for stage 2).
- [ ] **M3 — Tailoring + outreach drafting → approval queue**
- [ ] **M4 — Dashboard**
- [ ] **M5 — Orchestration (idempotent nightly jobs)**
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
