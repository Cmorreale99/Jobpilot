# Jobpilot Architecture V3 — the Project Story construction layer

**Date:** 2026-07-05 · **Status:** draft for review · **Supersedes:** the R1–R5 plan in `docs/REVIEW_LAYER_AUDIT.md` (its diagnosis stands; its remedy is upgraded)

---

## 0. The reframe

V2 built a truthful **claim ledger**: evidence → grounded claim-atoms → per-claim human review → resume. It works, and its guarantees are kept. But its output unit is wrong for the job:

- This is **not claim extraction.** Claims are inventory.
- This is **not resume bullet generation.** Bullets are a byproduct.
- The user is **never asked to approve 100+ raw claims.** (The live queue today: 148.)

V3's task is to construct **one coherent Project Story per project.**

**Core principle:** for every project, first define the actual problem space. Then select Actions as the steps taken to remedy that problem space. Then select Results as evidence that the remedy worked.

```text
Problem Space:  the real user/business/technical/process problem the project existed to solve
Actions:        the technical and strategic steps taken to remedy that problem space
Results:        the measurable, observable, or user-attested outcomes caused by those actions
```

- The Problem is **not a random extracted phrase.** It is the core reason the project mattered.
- The Actions are **not random implementation details.** They are the highest-leverage interventions taken against the Problem.
- The Results are **never invented.** Evidence-backed, directly user-attested, or marked missing.

**Blunt rule:** if a project does not have a real Problem, meaningful Actions, and a credible Result, it is not a Master CV project yet. It is evidence inventory, not resume material.

### Worked example — Jobpilot itself

> **Problem Space:** New grads and early-career candidates are struggling to navigate a noisy, inefficient, and psychologically demoralizing job market. The process requires repeatedly translating messy career history into targeted resumes, evaluating fit, applying, networking, and preparing for interviews. This creates friction, wasted time, inconsistent positioning, and false beliefs that the market is impossible rather than systematizable.
>
> **Actions:** Modeled the end-to-end job-search process as a system: master CV construction, source/evidence ingestion, job search, fit evaluation, tailored resume generation, application tracking, networking support, and interview preparation. Built an agentic pipeline that converts messy user career evidence into structured project stories, ranks opportunities, generates targeted materials from approved evidence, and supports repeatable execution across the job-search funnel.
>
> **Results:** Increased job-search efficiency by [X%] by automating [specific workflows]. Reduced monthly job-search/tooling costs by [$Y]. Increased interview rate by [Z×] across [N applications/timeframe]. *These metrics are not yet proven → marked `user_attestation_needed`, never invented.*

## 1. Where the story layer sits

```text
sources (Drive, GitHub, uploads — read-only, policy-scoped)
  → normalization → roster detection → HUMAN roster confirmation   (V2, unchanged)
  → chunk assignment (per-entity evidence, char spans)             (V2, unchanged)
  → claim extraction + PAR validation                              (V2, unchanged — output demoted to INVENTORY)
  → ★ PROJECT STORY SYNTHESIS (new, one story per confirmed entity)
  → ★ STORY REVIEW (human: ~12 cards, targeted questions, one decision each)
  → Master CV = snapshot of resume_ready stories                   (render unit changes: story, not claim)
  → tailoring / outreach / prep draw from approved stories + their bullets
```

Everything below the ★ rows is already built and keeps its guarantees: grounding, provenance, attestation, loud failure, cost controls, the evaluation harness.

## 2. Data model

New table `project_stories` (one row per confirmed entity, replaced idempotently like claims):

| column | meaning |
|---|---|
| `experience_id` | FK to the confirmed roster entity (unique — **the same project appears once**) |
| `status` | `draft → pending_review → resume_ready \| needs_problem \| needs_result \| needs_action \| evidence_only \| portfolio_inventory \| duplicate_needs_merge \| exclude_low_value` |
| `problem_space` | synthesized problem statement (text) + `problem_status` (`evidenced \| user_attested \| missing`) |
| `actions_json` | ranked strategic actions (see §3.2 shape) |
| `results_json` | ranked results (see §3.3 shape) |
| `bullets_json` | 1–3 candidate bullets, each with `component_refs` (only when resume_ready) |
| `questions_json` | the minimum targeted questions blocking readiness |
| `synthesis_hash` | fingerprint of the inputs (claims + evidence) — unchanged inputs skip re-synthesis (same economics as `extraction_hash`) |

Claims and evidence are untouched as tables; claims gain nothing. **Provenance chain:** bullet → story component → `claim_ids`/`evidence_ids` → chunks → source. Every rendered sentence still walks back to a file the user owns or a statement the user typed.

## 3. The synthesis contract (the LLM stage)

One DEEP-tier call per confirmed entity (~12 calls total for the current corpus; prompt-cached evidence block; `synthesis_hash` skip on re-runs). Input: the entity (name, kind, dates, aliases), its assigned evidence chunks, and its extracted claim inventory. Output: exactly the structure below. The model output is **draft**, never canonical — the human story review is the gate.

### 3.1 Problem Space

- Must be a complete, meaningful problem statement that explains why the project mattered.
- Higher-level than a one-line bug or implementation task.
- Must not be a filename, job header, tagline, or extracted fragment.
- **Must not invent business context.** Synthesis may *compose and elevate* what the evidence supports (this is the deliberate V3 relaxation from verbatim-only), but every composed problem carries `supporting_refs` into the evidence, and a problem the evidence cannot support is not written — it becomes:
  - `Status: needs_problem` + the targeted question: *"What user, business, operational, or technical problem did this project solve?"*
- A user's typed answer becomes a `user_attestation` evidence row (existing machinery) and the problem's status becomes `user_attested`.

### 3.2 Actions

- Select the strongest Actions that remedied the Problem Space; prefer high-leverage system-building over minor implementation details.
- **Group related implementation details under the same strategic action** — never 20 tiny actions when 3–5 strategic ones capture the work.
- Rank by career signal. Every Action must belong to this project (enforced structurally: the input contains only this entity's evidence — cross-project is unrepresentable, as in V2 extraction).
- Per action: `action_summary`, `technical_details`, `tools`, `evidence_refs` (claim/evidence ids), `confidence`, `career_signal_score`.

### 3.3 Results

- Evidence-backed or user-attested only. **No invented numbers. No metrics borrowed from another project. No vague impact when a measurable result exists.**
- Numbers remain under the V2 verbatim/number-factuality gates: a quantified result must show its metric verbatim in a cited chunk or carry a user attestation.
- No result → `Status: needs_result` + *"What measurable or observable outcome came from this project?"*
- Per result: `result_summary`, `metric?`, `evidence_refs` or `user_attestation_needed`, `confidence`, `result_strength_score`.

### 3.4 Resume-Ready hard gate

A story is `resume_ready` **only** if it has, all under the same `experience_id`:
- one evidenced or user-attested Problem Space,
- one or more approved Actions,
- one evidenced or user-attested Result.

Enforced three times: at synthesis (status assignment), at review (the approve control is disabled until the gate passes), and at render (`build_snapshot_content` refuses any entity without a resume_ready story — an incomplete story can never reach the docx, no matter what).

### 3.5 Best Project Story + candidate bullets

For resume_ready stories: one concise Problem paragraph, one technical-but-readable Action explanation, one evidence-backed Result — plus 1–3 candidate bullets, each tracing to selected P/A/R components, no unsupported metrics, no generic "improved efficiency" without mechanism and outcome, preferring technical depth combined with business/user impact.

### 3.6 Targeted questions

Non-ready stories carry only the **minimum** questions needed, drawn from: *what problem did this solve · what changed after it shipped · was this used by anyone besides you · did it reduce time/cost/manual work/errors/latency/risk · is there a measurable result, even approximate · include in Master CV or keep as portfolio inventory?*

### 3.7 Dedupe / merge

The same project appears once. Entity-level duplication is already solved by the roster (merge moves claims + evidence). Within a story, synthesis deduplicates overlapping actions and results across sources, preserves provenance for each, and keeps the strongest version. If synthesis detects that two *confirmed entities* are the same project, it emits `duplicate_needs_merge` naming the counterpart — resolved by the existing roster merge, never by a second card.

### 3.8 Ranking

Stories and components rank by: strength of Problem Space, technical depth, business/user relevance, measurable Result strength, relevance to target data/AI/analytics engineering roles, evidence quality, uniqueness, recency, credibility, seniority signal. The ranking orders the review queue (strongest stories first) and the rendered CV (per section).

## 4. Story review — the human layer

The dashboard's claim queue is replaced by **~12 story cards**, each one decision:

```text
┌─ Project Story: BoardGameGeek async data pipeline ── resume_ready ✓ ─────────┐
│ Problem: Board-game data lived behind a rate-limited XML API…  [evidenced]   │
│ Actions (3, ranked): async ingestion architecture · retry/backoff system ·   │
│                      normalized storage layer                    [12 refs]   │
│ Result: sustained full-catalog sync within API limits…         [evidenced]   │
│ Bullets: ▸ 3 candidates                                                      │
│ [ Approve story ]  [ Edit… ]  [ Portfolio inventory ]  [ Exclude ]           │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Project Story: investment-decision-workflow-engine ── needs_problem ───────┐
│ “What user, business, operational, or technical problem did this solve?”     │
│ [___________________________________________________]  [ Answer & re-ready ] │
│ [ Portfolio inventory ]  [ Exclude — low value ]                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

Decision semantics (all over existing machinery):
- **Approve story** → story becomes canonical; the claims its components cite are approved; uncited claims remain inventory (no status churn, no golden-set pollution — a rejection still only ever means "false").
- **Answer a question** → typed text becomes attested evidence, synthesis re-evaluates the gate locally (no LLM call needed for a single attached statement).
- **Edit** → per-component drill-down (existing edit-attest flow) for wording, metric, or component swaps.
- **Portfolio inventory / Exclude** → the entity keeps its evidence and claims but never renders; excluded is terminal like roster discard.

Estimated effort for the current corpus: 12 cards, ~7 questions to answer → **~15 interactions, minutes not hours.**

## 5. Final output of every synthesis run

The run report (and `python -m app.tools.eval_extraction` successor `eval_stories`) always ends with:

1. Resume-ready projects.
2. Projects needing only a Result.
3. Projects needing a Problem.
4. Evidence-only / portfolio inventory projects.
5. Duplicate projects merged.
6. Excluded low-value projects.
7. The highest-leverage questions to ask the user.

Recorded per run in `validation_runs` (kind `story_eval`), extending the Phase-4 harness: `resume_ready_rate`, `questions_outstanding`, `invented_metric_count` (must be 0 — checked by the number gate against each story's cited evidence), `orphan_component_count` (components citing nothing — must be 0).

## 6. What V3 changes vs. keeps

| Keeps (V2 guarantees) | Changes |
|---|---|
| Evidence ingestion, normalization, roster + human confirmation, chunk spans | Review unit: claim → **project story** |
| Claim extraction + PAR validator (now feeding inventory, not the queue) | Render unit: approved claims → **resume_ready stories** |
| Verbatim grounding for quotes/metrics; number-factuality gate | Problem statements may be **synthesized** (composed from evidence, ref-backed, or `needs_problem`) |
| Attestation provenance, terminal reject-with-reason, golden set | Flags: problem-absence is story-level status, not claim noise |
| Cost controls (batching, caching, fingerprint skips) + `synthesis_hash` | The Missing-Results queue, per-claim approve 409 gate, and the 148-card UI retire |
| Loud, isolated failure; evaluation harness | `shelved` status from the review-layer audit is **no longer needed** — uncited claims simply stay inventory |

## 7. Implementation plan

- **S1 — domain (pure):** `ProjectStory` model + status machine, component shapes, the resume_ready gate, story fingerprint, structural checks (orphan refs, cross-entity refs, unsupported numbers → story fails synthesis validation and records why).
- **S2 — synthesis:** `StorySynthesizer` protocol; deterministic heuristic default (assembles a draft story from the entity's best existing claims — offline/tests); LLM DEEP implementation with the §3 contract as its prompt; strict-JSON, grounding-checked, loud failure; migration `0012` (`project_stories`).
- **S3 — service + run:** `run_story_synthesis` (per confirmed entity, hash-skip, validation-logged); question-answer endpoint that attests and re-gates without re-synthesis.
- **S4 — review API + dashboard:** `GET /stories`, `POST /stories/{id}/approve|edit|answer|classify`; the story-card UI replaces the claims queue (claim drill-down retained inside the card).
- **S5 — render + tailoring:** snapshot takes resume_ready stories (P-A-R sections + bullets); tailoring selects among approved stories/bullets by job fit — highlight traceability now points at story components.
- **S6 — eval:** `eval_stories` metrics + exit tests: the live-shaped corpus (12 entities / 148 claims) reviews to completion in ≤15 interactions; a story without a credible Result cannot render; no invented metric survives; the Jobpilot worked example synthesizes with its unproven metrics marked, never filled.

## 8. Migration from today's state

The 148 pending claims are **not** a review debt anymore — they are exactly the inventory S2 consumes. First synthesis run turns them into ~12 story cards; nothing already decided is touched (the one existing rejection stays golden-set data); the claim queue UI is removed once the story cards ship. No data loss, no re-extraction, no new LLM spend beyond ~12 DEEP synthesis calls.
