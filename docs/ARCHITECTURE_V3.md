# Jobpilot Architecture V3 — the Project Story construction layer

**Date:** 2026-07-05 · **Revision 2** (incorporates `docs/V3_AUDIT.md` §22 modifications a–j; Revision 1's audit score line was 6/7/2/6/4/3/4/4/3/4) · **Supersedes:** `docs/REVIEW_LAYER_AUDIT.md`

---

## 0. The problem, and the reframe

Jobpilot's story layer is an **evidence-to-career-story compiler**: it turns a noisy, redundant, heterogeneous personal corpus into a small set of **verifiably true**, high-leverage, project-level career stories — conditioned on target roles, inside a review budget of minutes. Every story sentence traces to owned evidence or a typed attestation; elicitation via the fewest highest-value questions is the **primary** path for evidence-sparse projects (the live corpus: 111 of 148 pending claims carry no problem — asking is the norm, not the fallback); the process is incremental and never overrides a human decision; and **the approved-story snapshot is the only input to every downstream consumer** — rendering, tailoring, outreach, matching, and interview prep alike.

V2 built a truthful **claim ledger**: evidence → grounded claim-atoms → per-claim review. Its guarantees are kept. But its output unit is wrong for the job:

- This is **not claim extraction.** Claims are inventory.
- This is **not resume bullet generation.** Bullets are a rendering, never a source of truth.
- The user is **never asked to approve 100+ raw claims** — not in the queue, and not one click deep.

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
> **Results:** Increased job-search efficiency by [X%]. Reduced monthly tooling costs by [$Y]. Increased interview rate by [Z×]. *These metrics are not yet proven → the card asks for them; they are never invented.*

## 1. Where the story layer sits

```text
sources (Drive, GitHub, uploads — read-only, policy-scoped)
  → normalization → roster detection → HUMAN roster confirmation        (V2)
  → chunk assignment (per-entity evidence, char spans; UNASSIGNED
    chunks are a surfaced queue, not a log line)                         (V2 + Phase 1)
  → claim extraction + PAR validation — ROSTER MODE ONLY (the legacy
    per-file fallback is DELETED in Phase 0; no confirmed roster ⇒
    extraction refuses loudly)                                           (V2, closed)
  → ★ STORY READINESS + SYNTHESIS (one story per confirmed entity)
  → ★ STORY REVIEW (human: ~15 cards, targeted questions, one decision each)
  → Master CV = immutable snapshot of APPROVED stories                   (render unit: story)
  → tailoring / outreach / matching / interview prep read ONLY the
    approved-story snapshot
```

**Corpus reality this must serve** (live, audited): **15 confirmed entities** — 12 with pending claims, 3 claim-less (`Jobpilot`: 116 chunks, 0 claims after extraction failures; `DS4635`: 10 chunks, 0 claims; `Paper recommender system`: no evidence). A claim-less-but-evidence-bearing entity still gets a card (readiness: everything missing; primary prompt: re-extract or classify); an evidence-less entity gets an exclude/keep prompt. "One story per confirmed entity" means 15, not ~12.

## 2. Data model

### 2.1 Two axes, not one status (audit mod a)

Revision 1 conflated LLM draft, gate diagnosis, human classification, and approved canon in a single ten-value status enum. V3 separates **machine readiness** (derived, recomputed, never authoritative) from **human review state** (stored, explicit):

**`review_status`** — the only lifecycle enum, four values:

```text
draft → pending_review → approved | excluded
```

- `approved` is a **human act with a timestamp** (`reviewed_at`). Nothing machine-assigned can ever equal it — `resume_ready` is a precondition for approval, never a substitute.
- `excluded` requires a retained reason + timestamp (an inclusion decision — symmetric with claim rejection, which already requires one). "Portfolio inventory" is an exclusion reason, not a separate status; the evidence and claims are kept, the story never renders.
- Roster events cascade: merging or discarding an entity **invalidates its story** (back to `draft`; a prior approval is recorded in the decision log). Re-synthesis **never replaces** an approved story or a story carrying answers — refreshing an approved story requires an explicit, logged un-approve first. This resolves Revision 1's "replaced idempotently" ambiguity in the only safe direction.

**`readiness`** — a derived struct, computed from the story's components at read time, never persisted as a scalar status:

```jsonc
{
  "problem":  "evidenced | user_attested | missing",
  "actions":  2,                        // count of selected actions
  "result":   "evidenced | user_attested | missing",
  "resume_ready": false,                // problem != missing AND actions >= 1 AND result != missing
  "missing": ["problem", "result"],     // compound gaps representable (live case: personal-knowledge-os 28/2/0)
  "questions": [ ... ]                  // derived from `missing` at read time — never persisted
}
```

Revision 1's statuses collapse into this: `needs_problem`/`needs_result`/`needs_action` are entries in `missing`; `evidence_only` and `portfolio_inventory` merge (a distinction without a behavioral difference); `duplicate_needs_merge` is **not a status** — duplication is a relation, reported by the dedupe pass (§3.7) as merge prompts, so a duplicate that also lacks a Result loses neither fact.

### 2.2 `project_stories` (migration `0012`, at the end of Phase 2)

| column | meaning |
|---|---|
| `experience_id` | FK to the confirmed roster entity, **unique** — the same project appears once |
| `review_status` | the four-value enum above |
| `reviewed_at`, `decision_note` | approval/exclusion timestamp; exclusion reason required and retained |
| `problem_text`, `problem_refs` | composed problem statement + supporting **claim ids** |
| `actions_json`, `results_json` | selected components — each carries a stable `component_id`, `claim_ids`, text |
| `synthesis_hash` | input fingerprint (claims + evidence); unchanged inputs skip re-synthesis (same economics as `extraction_hash`) |

**Provenance rule (audit mod e): story components may cite `claim_ids` only.** Claims stay the relational provenance and approval substrate; the JSON is presentational. Every component walks component → claim ids → `claim_evidence` links → chunks (char spans) → source, so nothing at the story layer can cite provenance that bypassed PAR validation, and component-level provenance stays queryable through real tables instead of un-FK'd blobs.

**Deliberately not persisted** (audit mods g, h): `confidence` / `career_signal_score` / `result_strength_score` (LLM self-grades — cut; ranking is deterministic, §3.2), `questions_json` (derived from readiness; only *answers* persist, as attestations), `bullets_json` (deferred — the renderer derives bullets from P/A/R components deterministically, exactly like V2's `render_claim_bullet`; persisting LLM bullet prose as canon is a regression, not a feature).

**Containment invariant, now explicit:** composed story text never enters the `evidence` table. Only user-typed answers do.

**Normalizer versioning (audit §12):** chunk spans are offsets into normalizer output, so the normalizer gains a `NORMALIZATION_VERSION` stamped onto evidence rows — a normalizer change becomes detectable instead of silently dangling every `#chars=` ref.

## 3. Story construction

### 3.1 Problem Space

- A complete, meaningful problem statement explaining why the project mattered; higher-level than a one-line bug or implementation task; never a filename, job header, tagline, or fragment. (The live corpus fails this bar today: `personal-knowledge-os`'s two "problems" are one-line pgvector bugs; entity 9 renders with a `streamlit` script bug as its only Problem. Under V3 both read as `missing` — and the card asks.)
- **Composition is allowed but never self-certifying** (audit mods e, j; red-team 10). Synthesis may compose and elevate what the evidence supports, but:
  1. the composed text must cite supporting `claim_ids`;
  2. the card **always renders the cited chunk quotes underneath the composed text** — the reviewer approves a comparison, not an `[evidenced]` label;
  3. a deterministic lexical-overlap check flags weakly supported compositions (`problem_support: weak`) — cheap, testable, no LLM judging LLM;
  4. **approving the story attests the problem text**: it becomes a `user_attestation` evidence row addressed `story:{id}:problem`. Post-approval, "every rendered sentence walks back to a file the user owns or a statement the user typed/approved" is literally true — Revision 1's version of that guarantee was false for composed Problems.
- Insufficient evidence ⇒ `missing` + the targeted question: *"What user, business, operational, or technical problem did this project solve?"* Never invented. A typed answer is structurally validated (§3.6), then attested.

### 3.2 Actions

- The strongest interventions against the Problem Space; related implementation details grouped under one strategic action — never 20 tiny actions when 3–5 strategic ones capture the work.
- Per action: `component_id`, `action_summary`, `technical_details`, `tools`, `claim_ids`.
- Cross-project actions are unrepresentable at synthesis (per-entity input) **and** structurally checked: a component citing another entity's claim fails S1 validation. Container-entity blending — which per-entity input *cannot* see, and which the live portfolio entity proves is real — is handled by the cross-entity dedupe pass (§3.7).
- **Ranking is deterministic** (audit mod g), reusing the leverage score: verified quantified result 4 > qualitative/attested result 3 > problem-bearing action 2 > bare action 1; tie-breakers: no integrity flags, more evidence links, recency (`modified_time`/`pushed_at`), shorter text. Code, not model output; testable; orders candidates and pre-selection. No LLM self-scores anywhere.
- **When a user attests a problem, selection re-ranks against it** (audit §5): actions with token overlap against the attested problem outrank unrelated ones — deterministic, local, no re-synthesis call. The P→A arrow is architecture, not prompt vibes.

### 3.3 Results

- Evidence-backed or user-attested only. No invented numbers; no metrics borrowed across projects; no vague impact where a measurable result exists. Quantified results keep V2's verbatim gate: the metric appears verbatim in a cited chunk or carries an attestation.
- **Results must address the Problem** (audit mod j): a deterministic story-level coupling check (token/tag overlap between selected Result and Problem — the successor of V2's `resolves`, restated where it matters). A mismatch is not a flag-wall; the card asks the coupling question: *"Is this outcome from the same piece of work, or is this two stories?"*
- **Cross-source metric conflicts become questions, never silent picks** (audit mod j; red-team 8): when sources disagree on the same result (the existing fixture pair: "cutting export failures in half" vs. "…in half across all regions"), a deterministic conflict detector surfaces *"your sources disagree — which is current?"* with both quotes and their source dates. "Keep the strongest version" is banned as a rule — it is a pick-the-biggest-number generator; recency is the suggested tie-break, the human is the actual tie-break.
- No result ⇒ `missing` + *"What measurable or observable outcome came from this project?"*

### 3.4 The resume-ready gate — enforced in code, three real places (audit mod a)

A story may be **approved** only if readiness holds: Problem ≠ missing, ≥ 1 Action, Result ≠ missing, every component citing this entity's claims.

1. **Domain:** a pure gate function over the readiness struct — unit-testable, no I/O.
2. **API:** `POST /stories/{id}/approve` returns a **server-side 409** when the gate fails. The disabled button is UX; the 409 is the gate.
3. **Render:** `build_snapshot_content` refuses any entity without an **approved** story — approved, not `resume_ready`; a gate-passing draft no human saw never renders. This is a code change with named exit tests (T5/T6/T12): today's builder has no P-A-R bar at all and an approved action-only claim renders — that behavior is deleted, and the render path reads the snapshot it stamps rather than re-querying live rows.

Downstream is gated by construction: tailoring, outreach, matching, and prep read only the approved-story snapshot (§6), so "incomplete project in a cover letter" is unrepresentable rather than discouraged.

### 3.5 Integrity flags at the story layer (audit mods b, d; §9)

Phase 0 splits the validator families. **Absence flags** (`problem_missing`, `problem_not_pain_point`) stop persisting on claims — absence is readiness data, not a claim defect. **Integrity flags** (`action_tool_not_in_text`, `result_problem_coupling`, `duplicate_outcome_span`, verbatim failures) keep blocking:

- The per-claim 409 gate **stays** for integrity flags. (Revision 1 retired it wholesale — a truthfulness regression; 78 of the 189 live flags are integrity flags with nowhere else to go.)
- Story approval **does not bulk-approve flagged claims.** The approve cascade moves cited *unflagged* claims through the normal state machine; a cited claim carrying an integrity flag surfaces on the card as a plain-language per-component prompt ("this metric's source doesn't fully support the wording — confirm or edit it"), resolved by story-component edit-attest. Never a validator string; never the raw-claims UI.
- Fatal synthesis failures (orphan refs, cross-entity refs, unsupported numbers in composed text) **quarantine the draft**: it stays `draft` with the failure recorded in `validation_runs` and shown in the run report and debug view — visible state, not a swallowed log line.

### 3.6 Targeted questions and attestation (audit mods d, h, j)

- Questions are **derived from the readiness struct at read time** — never persisted, never stale. Taxonomy: missing problem · missing result · P/R coupling mismatch · cross-source metric conflict · used-by-others · measurable-outcome-even-approximate · include-or-inventory.
- Typed answers are **structurally validated before acceptance** (T16): the same deterministic specificity checks extracted problems face (minimum substance, not a fragment, not an artifact pattern) apply to attested text. V2 currently validates machine text harder than human text — backwards, fixed here. A one-word answer cannot flip `resume_ready`.
- Accepted answers persist as `user_attestation` evidence rows addressed **`story:{id}:{component}`** (audit mod d) — story-scoped attestation, a small extension of the existing claim-scoped `claim:{id}:{field}` scheme. The claim-scoped path survives for claim-level edits, but the story card never routes the user through raw claims or flag strings.
- Answering re-computes readiness locally — no LLM call.

### 3.7 Deduplication — deterministic, service-level (audit mod c)

Revision 1 asked per-entity synthesis to detect cross-entity duplicates. That is structurally impossible — each synthesis call sees exactly one entity — and it is replaced:

- **A deterministic cross-entity evidence-overlap pass** (pure function in `domain/roster.py` + service wrapper) runs before synthesis: entities sharing outcome spans, verbatim quotes, or high evidence-fingerprint overlap produce **merge prompts** — a relation (`entity_a`, `entity_b`, the shared evidence), resolved through the existing roster merge, never a second card. The live proof case — "Top 3 out of 100+ teams" sitting under both `OneWorld` and the portfolio entity (claims 472/513) — is exactly what this pass catches and nothing in the current system can.
- **Within-story dedupe** stays in synthesis (merge overlapping actions/results, preserve each side's provenance) *plus* a deterministic post-check: one outcome span backs at most one component — the V2 rule restated at story level.
- **Final-CV duplicate enforcement** (previously enforced nowhere): render refuses a snapshot where the same metric string appears under two stories, and `eval_stories` tracks `duplicate_story_count` (must be 0). Testable (T10/T11), not aspirational.

### 3.8 Synthesis engines (audit mods h, i)

- **Heuristic (default, offline, free): selects and gates only — it never authors prose.** It picks leverage-ranked claims per component, computes readiness, derives questions. It writes no problem statements and no summaries — deterministic template authorship is slop with a straight face, and with credits exhausted it is what would run against live data first.
- **LLM (DEEP tier, flag-gated, ships LAST):** authors composed problem statements and strategic action grouping under the §3.1–3.3 contract; strict-JSON, grounding-checked, quarantined on failure; one call per entity with prompt-cached evidence and `synthesis_hash` skips (~15 calls for the live corpus, with a deterministic per-entity evidence budget and logged exclusions — red-team 12). It runs against live data only after the card UI, quote rendering, and gates exist and are tested: **the human gate ships before the prose generator.**

## 4. Story review — the human layer

~15 cards, one decision each. A card shows: the composed/selected P-A-R with **evidence quotes rendered under every component — including the composed Problem's supporting quotes** (audit mod e); readiness with its missing list; plain-language prompts for integrity issues; derived questions with inline answer fields; the deterministic ranking's pre-selection.

Decision semantics:

- **Approve** → server-side gate (§3.4) → `approved` + `reviewed_at`; the problem text is attested (`story:{id}:problem`); cited unflagged claims are approved via cascade and **marked `decided_via=story`**, so the golden set never counts cascade approvals as direct human claim verdicts (audit §12/§15); uncited claims stay inventory, untouched.
- **Answer** → structural validation → attestation → readiness recomputed locally.
- **Edit a component** → **story-component edit-attest** (`story:{id}:{component}`), quotes alongside — never the raw-claims UI, never flag strings (audit mod d; Revision 1's Edit path would have re-exposed 28 raw, flag-walled claims one click deep on `personal-knowledge-os`).
- **Exclude** (including "keep as portfolio inventory") → `excluded` with a retained reason; evidence and claims untouched; never renders.
- **Merge prompt** (from §3.7) → routes to the existing roster merge; the merged-away entity's story is invalidated.

**Review surface, honestly estimated on the live corpus:** 15 cards ≈ 7 plausible direct approvals + ~9–13 typed answers (5 entities need a Problem, 4 need a Result, 2 need both) + 3 classify/exclude decisions ≈ **25–30 interactions, one sitting.** (Revision 1 said ~15; the audit corrected the arithmetic. An "interaction" is defined: cards + unanswered questions + merge prompts — so the exit criterion is measurable.)

**Debug view — explicitly outside the review flow:** the claim inventory, validator output, `validation_runs`, slop metrics, and unassigned-evidence counts live in an advanced view. Diagnostics, not decisions.

## 5. Run report + evaluation

Every synthesis run reports and records (`validation_runs` kind `story_eval`): approved · ready-awaiting-review · missing-Result-only · missing-Problem · excluded/inventory · merge prompts · quarantined drafts · the highest-leverage outstanding questions. Metrics: `resume_ready_rate`, `questions_outstanding`, `invented_metric_count` (= 0, checked against cited evidence), `orphan_component_count` (= 0), `duplicate_story_count` (= 0), `cross_source_conflict_count`, and the defined review-interaction count.

**The live-shaped fixture comes first** (audit mod i): a corpus fixture matching production's actual shape — 15 entities including the 3 claim-less ones, ~148 claims with the real per-entity Problem/Result distribution — is Phase 2's first commit, and T1–T16 (`docs/V3_AUDIT.md` §17) become per-phase exit tests instead of an S6 afterthought. V2's documented eval failure — green on pre-labeled fixtures no real document resembles, garbage in production — does not get a sequel.

## 6. Downstream — fully specified (audit mod f)

- **Story→`MasterCv` adapter:** the approved-story snapshot maps to `ParClaim`s with `source_ref = story:<id>:<component_id>` (mirroring `claim:<id>`). **All four** snapshot consumers — matching, tailoring, outreach, interview prep — read this adapter; Revision 1 named only tailoring.
- **Stable component addressing:** `component_id`s persist across re-synthesis of unapproved drafts and freeze at approval — downstream highlight references cannot dangle.
- **Number-gate source:** the tailoring gate resolves allowed numbers against **cited evidence chunks and attestations** (a resolver walk from component refs) — not against generated story text. Generated text never grounds further generation.
- **Outreach body gate (new):** the same `unsupported_numbers` check runs on outreach subject + body before a draft enters the approval queue — closing the audited hole where the one artifact that actually leaves the machine had no number gate at all.
- **Pipeline empty-condition:** `run_application_pipeline` treats "zero approved stories" as an explicit, loudly logged skip naming story review as the blocker — not an incidental empty-claims no-op that idempotency makes look normal.
- **Snapshot versioning:** story-shaped snapshots are a new snapshot kind; old claim-shaped versions stay readable; fingerprint lineage restarts and the migration notes say so.

## 7. Implementation plan (audit-reconciled ordering)

Everything through Phase 4 is offline-buildable (heuristics, fake LLM client, fixtures). The only credit-gated step is the final live synthesis run — **last**.

- **Phase 0 — stop the slop at its sources (days):** delete the legacy per-file extraction fallback — no confirmed roster ⇒ extraction refuses loudly (today the fallback auto-mints `CONFIRMED`, render-eligible, file-shaped entities with zero human review); split absence flags from integrity flags — absence stops persisting on claims, integrity keeps the 409; a data migration strips stored absence flags from unreviewed claims. Invert the test that asserts the fallback works.
- **Phase 1 — capsule foundation (verify, don't rebuild):** the roster already provides proposal/confirmation/merge/aliasing/span assignment. Add the deterministic cross-entity evidence-overlap pass (§3.7) and the unassigned-evidence surface (red-team 3: unassigned chunks are a queue, not a log line).
- **Phase 2 — story domain (pure, offline):** `domain/project_story.py` — readiness struct, the gate, question derivation, deterministic leverage ranking, structural checks, story fingerprint, the coupling and conflict detectors. **Live-shaped fixture first.** Migration `0012` at phase end. Merge/discard invalidation and never-replace-approved semantics in the repository.
- **Phase 3 — review API + cards:** heuristic synthesis service (hash-skip, quarantine, validation-logged); `api/stories.py` (approve with the server-side 409, answer with structural validation, component edit-attest, exclude-with-reason); story cards with quotes under every component. **Additive:** the claims queue survives until T1–T4 pass, then is deleted.
- **Phase 4 — render from approved stories:** `build_snapshot_content` over approved stories + the render-time gate + duplicate-metric refusal; story P/A/R mapped into the **frozen** renderer contract (the template is never touched); snapshot kind versioned.
- **Phase 5 — downstream, then the LLM, last:** the §6 adapter and gates; then the LLM `StorySynthesizer` behind a flag, fake-client-tested, with one live run scored by `eval_stories` **before** any card is reviewed.

## 8. Migration from today's state

The 148 pending claims are not review debt — they are exactly the inventory Phase 2 consumes. No re-extraction, no data loss; the one existing rejection stays golden-set data; roster, evidence, attestations, cost controls, and the evaluation harness carry over unchanged. Total new LLM spend to reach a reviewed, story-shaped Master CV: **~15 DEEP calls, once, at the very end.**
