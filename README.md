# JobPilot

JobPilot is a single-user, mostly-automated job-search operating system. It compiles a canonical Master CV from the job seeker's own artifacts, finds fresh high-fit roles, ranks them against that CV, drafts tailored application materials, queues personalized outreach for approval, detects interview invitations, and generates interview prep packets — all surfaced on one dashboard.

It optimizes for **fit and quality per application, not volume.** The goal is to turn job searching into a disciplined, inspectable pipeline where the user can see what was found, why each role matched, what materials were generated, and what action is waiting for approval — with a hard guarantee that nothing leaves the machine unless a human approves it, and nothing on a resume was invented.

---

## The problem

The job market has become adversarial and asymmetric, and early-career candidates feel it worst.

- **Employers automate; individuals don't.** Companies run automation across sourcing, JD generation, ATS filtering, and screening. The individual applicant answers with manual copy-paste, one resume rewrite at a time.
- **Translating messy history into targeted materials is expensive and repetitive.** A person's real experience is scattered across GitHub repos, Drive docs, old resumes, and half-remembered projects. Turning that into a role-specific resume — accurately — is slow, and the tenth application looks worse than the first.
- **Volume tooling makes it worse.** "Apply to 500 jobs" tools flood the funnel with low-fit, generic applications, degrade positioning, and train candidates to believe the market is impossible rather than *systematizable*.
- **Resume inflation is a trap.** The fastest way to stand out is to exaggerate. That is exactly the thing that collapses in an interview and burns trust. Truthful, evidence-backed material is slower to produce and therefore rarely produced.

The result is friction, wasted time, inconsistent positioning, and demoralization — a process that *feels* random but is actually a system that no one has built the tooling for.

## What JobPilot solves

JobPilot is a counterweight for the individual. It treats the job search as an end-to-end system and automates the parts that are mechanical while keeping the human in the loop for every judgment and every outbound action.

Its central bet is a **truth-preserving evidence pipeline**: instead of asking a model to "write me a resume," JobPilot ingests the user's own artifacts, structures them into verifiable **Project Stories** (Problem → Actions → Results), makes the human confirm and approve each one, and then treats that approved set as the *only* source of truth for everything downstream — matching, tailoring, outreach, and interview prep. Every sentence that reaches a resume or an email traces back to a file the user owns or a statement the user typed and approved.

### Business impact

- **Higher hit-rate per application.** Two-stage matching + evidence-grounded tailoring means fewer, better-targeted applications instead of spray-and-pray.
- **Zero fabrication risk.** Numbers, claims, and contacts are gated against cited evidence at every chokepoint. Ungrounded content is dropped, not shipped — the resume survives the interview because it was always true.
- **Compounding leverage.** The Master CV is built once and reused across every role; tailoring is a cheap projection of approved stories, not a from-scratch rewrite each time.
- **Cost-controlled automation.** Expensive model work (deep re-rank, synthesis, drafting) sits behind tiered models, prompt caching, fingerprint-based skip logic, and grounding gates that fail closed to deterministic heuristics — so a flaky or costly LLM call never sinks a nightly run.
- **Trust by construction.** Nothing sends without approval; inbox access is scoped to interview detection only; personal texts and general email are never mined. The user can audit every decision the system made.

---

## How it works (V3 story pipeline)

```text
sources (Google Drive, GitHub, uploads — read-only, policy-scoped)
   → normalization
   → roster detection ─────────────► HUMAN confirms/renames/merges entities
   → chunk assignment (per-entity evidence; unassigned chunks are a surfaced queue)
   → claim extraction + PAR validation (roster mode only — no confirmed roster ⇒ refuse loudly)
   → STORY SYNTHESIS (one Project Story per confirmed entity)
   → STORY REVIEW ─────────────────► HUMAN approves / attests / excludes (~15 cards)
   → Master CV = immutable snapshot of APPROVED stories
        │
        ├── Application pipeline: fetch jobs → score all → shortlist → deep re-rank top-N
        │                         → tailor materials → research contact → draft outreach → approval queue
        │
        └── Interview scan: scoped Gmail query → detect invite → verify provenance
                                  → confirm queue → prep packet
```

Two scheduled jobs run **independently** — a failure in one never blocks the other:

1. **Application pipeline** — snapshot approved stories as the Master CV version, fetch recent jobs, score every role, deep-rerank the top matches with a rationale, tailor materials from approved evidence, research a contact, and draft outreach into the approval queue. **With zero approved stories the run stops before matching and names story review as the blocker** — nothing is ever generated from an unreviewed ledger.
2. **Interview scan** — a scoped, read-only Gmail query for interview invitations, a conservative detector that requires a verbatim body quote, hard provenance verification (re-fetch the message, assert the quote is a real substring), a human confirm queue, and prep-packet generation on confirm.

### The core discipline: stories, not claims

The unit of truth is the **Project Story**, one per confirmed real-world entity — not a pile of extracted claims and not a pre-written resume bullet.

- **Problem Space** — the real user/business/technical/process problem the project existed to solve. Higher-level than a one-line bug; never a filename or fragment.
- **Actions** — the highest-leverage steps taken against that problem.
- **Results** — measurable, observable, or user-attested outcomes. **Never invented** — evidence-backed, directly attested, or marked missing (and the card asks).

A story is only **resume-ready** when it has a real Problem, ≥1 Action, and a credible Result. That gate is pure code, re-enforced at three chokepoints: the review API (HTTP 409 on force-approve), the story-shaped snapshot builder, and the docx renderer. Readiness is *derived* at read time; the only stored lifecycle is `draft → pending_review → approved | excluded`, and re-synthesis never overwrites a human decision.

---

## Guardrails (non-negotiable)

- **Truthfulness.** Tailored materials and outreach derive only from approved Master CV data. Numbers are gated against cited evidence; ungrounded prose fails closed to the deterministic heuristic. Never invent experience, metrics, or contacts.
- **Data minimization.** Master CV sources are Google Drive, GitHub, and explicit uploads only. **No mining of personal texts or general email content.** Inbox reads are scoped to interview detection and gated by a flag.
- **Approval before outbound.** Outreach drafts wait in an approval queue by default; sending is the single exit point and goes through the state machine only. Confirm before real sends, first-time paid calls, and deletions.
- **Secrets & tokens.** Env only; `.env` git-ignored, `.env.example` committed. OAuth tokens encrypted at rest (Fernet).
- **Compliant sources.** Prefer compliant job APIs. LinkedIn scraping stays disabled and explicitly flagged.

---

## Architecture principles

- **Mock-first.** Every external dependency (Drive, GitHub, jobs, mail, research, LLM) sits behind an interface with a fake + fixtures. The full pipeline runs offline with **zero real credentials** before any real API is wired.
- **Pure domain.** `app/domain/` imports nothing from `integrations/real`, `integrations/mcp`, or `llm/` — business logic (matching, ranking, state machines, story synthesis, validation) stays testable in isolation.
- **Idempotent nightly jobs.** Re-running never double-applies, double-sends, or duplicates rows; human decisions are never overwritten by a re-run.
- **Scheduler-free services.** Orchestration lives in plain service functions (`run_application_pipeline`, `run_interview_scan`); `app/scheduler.py` is a thin APScheduler wrapper that ports to EventBridge→Lambda untouched.
- **Postgres is the transactional source of truth** — never a warehouse for app data.

### MCP integration strategy

GitHub and Google Drive are accessed through their **MCP servers** behind stable internal interfaces (`GitHubClient`, `DriveClient`) — the rest of the app doesn't know whether data came from a fixture, an upload, or a live MCP session. Gmail (scoped send + interview scan), the compliant job source, and any LinkedIn adapter remain direct API clients. Mocks are the default for every factory.

## Repo layout

```text
app/
  main.py              # FastAPI entrypoint
  config.py            # typed env-driven settings
  db/                  # SQLAlchemy models, sessions, Alembic migrations (through 0013)
  domain/              # pure business logic: matching, roster, claims, project_story, state machines
  services/            # orchestration: pipeline, roster, extraction, synthesis, review, render, send
  integrations/
    base.py            # interfaces (JobSource, DriveClient, GitHubClient, MailClient, ResearchClient, ...)
    mock/              # fakes + fixtures — the default in dev/tests
    real/              # Gmail, compliant job API, flagged LinkedIn
    mcp/               # GitHub + Google Drive MCP client sessions
    uploads.py         # local uploads client
  llm/                 # all Anthropic calls: tiers, retries, JSON parsing, cost logging, extraction/synthesis/matching/drafting
  render/              # frozen V2 docx renderer (integrate, never rewrite)
  scheduler.py         # APScheduler triggers → service functions
templates/             # frozen resume_template.docx
tests/                 # fixtures + live-shaped corpus; offline, zero-credential
web/                   # Next.js + Tailwind dashboard (pipeline ledger)
PLAN.md  CLAUDE.md  docs/ARCHITECTURE_V3.md  .env.example
```

## Data model (source of truth: Alembic migrations, through `0013`)

Core: `users`, `oauth_credentials` (encrypted), `cv_sources`, `master_cv` (versioned snapshots), `jobs`, `job_matches`, `applications`, `outreach`, `interviews`, `prep_packets`.

Evidence + story layer: `experiences` (roster entities: `kind`, `status`, `aliases`, `merged_into_id`), `evidence` (citable chunks with char spans + `normalization_version`), `claims` (strict PAR), `claim_evidence` (field-per-link), `project_stories` (one per confirmed entity), `artifacts` (rendered docx), `validation_runs` (every validator/provenance/eval run logged).

Rules: claims trace to source evidence; jobs dedupe on `(source, external_id)`; application/outreach/interview stages are explicit validated state machines; a Master CV version is an immutable approved-stories snapshot.

## LLM layer

All Anthropic calls go through `app/llm/`: strict JSON prompting, fence stripping, schema validation, one retry on parse failure, timeouts, prompt caching for shared context, and token/cost logging. Two env-configured tiers — `ANTHROPIC_MODEL_BULK` (stage-1 scoring, bulk extraction) and `ANTHROPIC_MODEL_DEEP` (deep re-rank, synthesis, drafting, prep). No model string is hardcoded. Every LLM-backed stage sits behind a flag and falls back to a deterministic heuristic when disabled or on failure.

---

## Tech stack

**Backend:** Python 3.12, `uv`, FastAPI, SQLAlchemy 2.x, Alembic, Postgres, APScheduler, Anthropic Messages API, MCP client SDK.
**Frontend:** Next.js (app router, TypeScript), React, Tailwind CSS.
**Quality:** pytest, ruff, mypy.

## Local setup

```bash
uv sync
cp .env.example .env          # then fill in values
alembic upgrade head

uv run uvicorn app.main:app --reload      # API
uv run python -m app.scheduler            # nightly jobs (dev)  — --once for an immediate run
cd web && npm run dev                     # dashboard
```

Quality (run before every commit):

```bash
uv run ruff check . && uv run ruff format .
uv run mypy app
uv run pytest -q
```

The fixture-backed pipeline passes with **zero real credentials.** If real credentials are required to run tests, that's a bug.

## Key config flags (defaults reflect the safe path)

| Env var | Default | Effect |
|---|---|---|
| `LLM_ENABLED` | `false` | False = deterministic fake client (no API key). |
| `OUTREACH_AUTO_SEND` | `false` | False = drafts wait in the approval queue. |
| `GMAIL_ENABLED` | `false` | False = in-process mock outbox; nothing leaves the machine. |
| `INTERVIEW_INBOX_SCAN` | `true` | False disables all inbox reads. |
| `JOB_SOURCE_PROVIDER` | `mock` | `mock` fixtures vs `remotive` compliant public API. |
| `GDRIVE_MCP_ENABLED` | `false` | False = fixture-backed mock Drive. |
| `STORY_LLM_SYNTHESIS` | `false` | False = heuristic (verbatim selection); true = LLM composer (Problem + Action grouping; Results stay verbatim). |
| `CLAIMS_LLM_EXTRACTION` / `ROSTER_LLM_DETECTION` / `MATCHING_LLM_RANKING` / `TAILORING_LLM_DRAFTING` | `false` | Per-stage LLM swaps; each falls back to its heuristic. |
| `RESUME_PROFILE_PATH` | (empty) | Profile JSON for header/education/skills — empty means rendering fails loudly (503); header data is never invented. |

See `CLAUDE.md` and `.env.example` for the full set.

---

## Project scope & status

### V1 — Mock-first pipeline (complete)

- [x] **M0** — Scaffold: repo structure, `uv`/ruff/mypy/pytest, interfaces + mocks, fixtures, DB + Alembic, FastAPI entrypoint.
- [x] **M1** — Master CV from mocked Drive/GitHub/upload sources with provenance and idempotent versioning.
- [x] **M2** — Jobs + two-stage matching (bulk score → shortlist → deep re-rank), heuristic + LLM behind flags.
- [x] **M3** — Tailoring + outreach drafting into an approval queue, state machines, idempotent persistence.
- [x] **M4** — Next.js dashboard: single-sheet pipeline ledger + detail views.
- [x] **M5** — Idempotent nightly orchestration; scheduler-free services; independent job isolation.
- [x] **M6** — Real integrations behind flags, **live-verified**: encrypted OAuth store, OAuth flow + HTTP routes, GitHub + Google Drive MCP clients, Gmail send-behind-approval, compliant Remotive job source.
- [x] **M7** — Interview scan + prep packets: scoped read-only Gmail query, conservative detector, provenance verification, confirm queue, prep generation.

### V2 — Truthful claim ledger (complete)

- [x] **M8** — V2 schema + two-pass PAR extraction + PAR validator.
- [x] **M9** — Review layer: approve / edit-attest / reject, section picker, approved-only snapshots.
- [x] **M10** — Interview validation: hard provenance, confirm queue, mode guard, `validation_runs`.
- [x] **M11** — Frozen docx renderer wired to approved claims; artifacts + download.
- [x] **M12** — Dashboard: claims review queue, missing-results, confirm queue, docx download, posting links.
- [x] **M12.5–M15** — Audit hardening: normalization + structural gates + dedupe + loud failures; **project roster** (detect → human confirm → chunk assignment → per-entity extraction); tailoring from the ledger (V1 structuring path deleted, highlight claim ids + number-factuality gate); evaluation harness (slop metrics, golden set, reality regression fixtures).

### V3 — Project Story construction layer (feature-complete)

- [x] **M16** — Phase 0: per-file fallback deleted (extraction refuses without a confirmed roster), absence/integrity flag split, migration `0012`.
- [x] **M17** — Phase 1: deterministic cross-entity evidence-overlap pass → merge prompts; unassigned-evidence queue + manual assignment.
- [x] **M18** — Phase 2: story domain layer (`domain/project_story.py`), live-shaped corpus fixture, `project_stories` + normalizer versioning (migration `0013`), story repos with never-replace-approved + invalidation.
- [x] **M19** — Phase 3: heuristic story synthesis (quarantine + `synthesis_hash` skip), review API with the runtime resume-ready 409 gate, story-scoped attestations, roster merge/discard → `invalidate_story` cascade, dashboard story cards.
- [x] **M20** — Phase 4: render the Master CV from **approved stories** — story-shaped snapshot with render-time resume-ready gate + cross-story duplicate-metric refusal, through the frozen renderer; full V3 loop test with docx XML assertions.
- [x] **M21** — Phase 5a (downstream): Story→`MasterCv` adapter, pipeline consumes the approved-story snapshot, dispatcher for all four consumers, tailoring `highlight_refs` + evidence-grounded number gate, outreach-body number gate.
- [x] **M22** — Phase 5b (LLM synthesis, credit-gated): flag-gated `LlmStorySynthesizer` composes the Problem Space + groups Actions (Results stay verbatim), grounds every number against cited evidence, loud failure on LLM error; `eval_stories` scorecard. **Live-verified** once against the real u1 corpus: 15 stories, 8 resume-ready, invented/orphan metrics = 0.

> Status: V3 Phases 0–4 are merged to `main`; Phases 5a/5b are built and live-verified on `v3/phase-5` (awaiting review). The full test suite is green offline with zero credentials.

### Remaining cleanup (before V4)

- [x] Retire the legacy V2 **claims review queue** and the dashboard `POST /master-cv/snapshots` surface — the story layer supersedes both. Removed the claim review routes/service and the web claims card; kept snapshot *reading*, the section picker, render/download, and claim extraction, so old approved-claims snapshots stay readable.
- [ ] Merge `v3/phase-5` after review sign-off.

### V4 — Second Brain + on-demand tailoring (next)

The next horizon (see `docs/SECOND_BRAIN_AUDIT.md`) is **not** a rebuild — the evidence→story→approval spine stays. It adds four genuinely net-new capabilities, built repo-native on Postgres (the Graphiti graph-store fork was evaluated and **rejected** — it breaks the "Postgres is the source of truth" invariant, defaults to OpenAI, and has no offline path):

- [ ] **Second-Brain / Obsidian ingestion substrate** — frontmatter + wikilink parsing and vault-zone policy, so a personal knowledge vault becomes a first-class, policy-scoped evidence source alongside Drive/GitHub/uploads.
- [ ] **Sensitivity / disclosure model** — the one net-new *content-level* guardrail: a pure `disclosure_violations` predicate wired at the same three chokepoints the number/resume-ready gates occupy (story approval, render refusal, outbound drafting), catching efficacy-overclaim and sensitive-leakage that the number gate misses (e.g. unverified clinical-efficacy claims that carry no number).
- [ ] **Cost / retrieval-snapshot runtime layer** — a pre-flight token estimate, a fail-closed budget ceiling (today `CostTracker` only logs), content-hash dedupe, and a `RetrievalSnapshot` lock. Semantic retrieval via **pgvector in Postgres** (no second datastore) with a temporal edges table.
- [ ] **Paste-a-posting → optimized resume** — a dashboard surface that parses a pasted job posting into requirements, scores each approved Project Story against them, and assembles an optimized, still-fully-grounded resume on demand — turning the nightly pipeline into an interactive one.

## Definition of done (per horizon)

The pipeline runs on fixtures with zero real credentials and produces: a versioned Master CV from **approved stories**, ranked top-N matches with rationales, tailored materials, outreach drafts in the approval queue, an interview record from a fixture invite, and a generated prep packet — with every downstream artifact traceable to approved evidence, and every real integration dropping in behind a flag without touching domain logic.
