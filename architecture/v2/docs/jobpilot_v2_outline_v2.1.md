# JobPilot V2 — Outline v2.1 (Re-scoped)

V2 theme: **a correct, verified, properly rendered Master CV**. Everything that
consumes the Master CV (retrieval, tailoring, grading) is deferred to V3 —
garbage in, garbage out, so the input gets fixed first.

Changes from v2.0:
1. **Retrieval/matching/grading layer → V3** (with it: embedding + reranker +
   NLI model selection, the multi-agent research workflow, golden-set eval,
   tailored resumes, grades)
2. **Result evidence rule revised**: content gate replaces source-type ban —
   commits and comments ARE searchable and extractable
3. **New: extraction review layer** — every Claude-extracted P/A/R goes to a
   human review queue before becoming canonical

Priority order:
1. Evidence & claims schema (revised Result rules)
2. Extraction review layer (human-in-the-loop)
3. Interview hallucination validation
4. Docx rendering with template fidelity
5. Dashboard (queues + links)

---

## 1. Evidence & Claims Schema (strict PAR, revised)

### Strict PAR definitions

| Field | Definition | Validation rule |
|---|---|---|
| **Problem** | A pain point costing measurable business value | Must declare `cost_dimension`: `money \| time \| risk \| quality \| revenue`. Reject task-descriptions ("needed a pipeline" is a task; "manual ingestion cost 10 hrs/week" is a problem). |
| **Action** | Steps *the user* took to solve it | Must name concrete tools/tech. Any evidence source valid. |
| **Result** | Business impact of the action | Must be **quantified or explicitly qualitative-but-evidenced**. See outcome-statement rule below. |

### The outcome-statement rule (replaces the commit ban)

**Any source may be searched for Results — commits, PR comments, READMEs,
docs, Drive files. What is validated is the content, not the source.**

Result evidence must contain an **outcome statement**, not a **work statement**:

| Statement type | Example (same commit history) | Valid as |
|---|---|---|
| Work statement | "fixed dedup key mismatch across four carriers" | Action evidence only |
| Qualitative outcome | "eliminated duplicate shipment rows; ops no longer reconciles manually" | Result (`qualitative_evidenced`) |
| Quantified outcome | "cut ingestion failure rate from 12% to 0.5%" | Result (`quantified`) |

Deterministic validation:
- `result_kind = quantified` → `result_metric_json` populated AND the metric
  text appears **verbatim** in the cited evidence chunk
- `result_kind = qualitative_evidenced` → an outcome quote (stored on the
  claim_evidence link) appears **verbatim** in the cited evidence chunk
- `result_kind = missing` → no invention; claim routes to the Missing-Results
  queue (§2) where the user supplies the number (`user_attested`) or approves
  omission

The extractor's job is therefore two-pass over the same sources: pass 1 finds
work statements (Actions), pass 2 hunts specifically for outcome statements
(Results). The V1 bug — pasting work statements into Result fields — becomes a
validator rejection, not a prompt hope.

### Schema

```
evidence
  id, source_type (drive|github_commit|github_pr|github_readme|upload|user_attestation),
  source_ref (file id / commit sha / message id / PR number),
  chunk_text, created_at
  -- embedding column deferred to V3 (pgvector migration lands then)

claims
  id, experience_id,
  status (extracted|pending_review|approved|rejected),
  problem_text, problem_cost_dimension,
  action_text, action_tools[],
  result_text, result_kind (quantified|qualitative_evidenced|missing),
  result_status (verified|user_attested|unverified),
  result_metric_json (nullable),
  review_note (nullable), reviewed_at (nullable)

claim_evidence
  claim_id, evidence_id, field (problem|action|result),
  outcome_quote (nullable — required when field='result')
```

No CHECK constraint on source types. The content gate lives in the PAR
validator (code), where it belongs.

Master CV stays canonical as **structured JSON**, versioned. A master CV
version is a **snapshot of approved claims only**. Docx is a rendered view.

---

## 2. Extraction Review Layer (human-in-the-loop)

### Claim state machine

```
extracted → pending_review → approved
                           → rejected (with reason)
```

- Extraction (Claude over GitHub + Drive) writes claims as `extracted`; the
  PAR validator runs its deterministic checks and promotes passing claims to
  `pending_review`. Validator failures bounce back to re-extraction with the
  specific violation, once; second failure lands in the queue flagged.
- **Only `approved` claims** can enter a master CV version or reach the
  renderer. No exceptions, enforced at the version-snapshot query.

### Review card (dashboard)

Each pending claim renders P / A / R side-by-side with its cited evidence
quotes and click-through links to the source (commit URL, Drive file). Actions:

- **Approve** — claim becomes canonical as-is
- **Edit + approve** — user edits any field; edited fields get provenance
  `user_attestation` and the edit is stored as an evidence row (source_type
  `user_attestation`), so traceability survives the edit
- **Reject** — requires a reason (wrong experience, inflated, duplicate,
  not mine). Rejected claims are kept, not deleted.

### Missing-Results queue

Unchanged in spirit from v2.0: strong Problem/Action with `result_kind =
missing` asks the user for the impact number or approval to omit. Now it is
simply a filtered view of the same review layer.

### Why rejections are kept

Approve/reject/edit decisions are labeled data. In V3 they seed the golden
dataset (which claims are real, which extractions overreach) and calibrate the
extractor. The review queue is the annotation tool; using it does the labeling.

---

## 3. Interview Hallucination Validation (unchanged from v2.0)

1. **Hard provenance**: `interviews.gmail_message_id` NOT NULL +
   `evidence_quote` (verbatim trigger text). No message ID → no record.
2. **Verification**: before insert, re-fetch the message by ID and assert the
   quote is a substring of the real body. Fail → reject + log.
3. **State machine**: `detected → confirmed → scheduled`; dashboard confirm
   queue; prep packets only for `confirmed`.
4. **Mode guard**: real scheduler asserts it is not running against
   `MockMailClient` at startup.
5. `validation_runs` logging table stays in V2 — it covers PAR validator runs
   and interview verifications. (NLI/LLM-judge rows arrive in V3.)

Note the symmetry: interviews and claims now use the same pattern —
deterministic provenance check, then a human queue. One philosophy, two
surfaces.

---

## 4. Docx Rendering (Master CV)

Unchanged approach, narrowed scope:

- No custom MCP. `app/render/` inside the pipeline.
- Template extracted from the real CV: copy the actual `.docx` →
  `templates/resume_template.docx`, swap content for Jinja placeholders,
  styles/fonts/spacing/margins intact. Render with **docxtpl**.
- Renderer input: **approved claims only**, grouped by experience.
- V2 renders the **Master CV** (multi-page is fine — it's the master).
  Assert font family/size on output anyway.
- Tailored-resume constraints (one page, 3–4 bullets by rerank score) defer
  to V3 with the retrieval layer — but the renderer API takes a claim-selection
  list as input now, so V3 tailoring is a new selector, not a new renderer.

---

## 5. Dashboard V2

- **Claims review queue** (§2) with review cards
- **Missing-Results queue** (§2)
- **Interview confirm queue** (§3)
- Master CV docx download link (artifact pattern from v2.0, `artifacts` table
  kept but only `master_cv_docx` kind is populated in V2)
- `jobs.url` captured at ingestion and linked on every job/application card
  (store original + canonical employer URL when resolvable)

---

## Deferred to V3 (in dependency order)

1. **Model-stack selection** — the multi-agent research workflow
   (`.claude/agents/` scout/auditor/synthesizer kit) is already committed and
   runs as V3 Phase 0. Do not run it in V2; its golden set should be built
   from V2 review-queue data.
2. pgvector migration + embedding column on `evidence`
3. Requirement extraction, retrieval, rerank, coverage map
4. Grading rubric + grade breakdowns
5. Tailored resumes (selector over the V2 renderer) + per-job artifact links
6. NLI entailment screen + LLM-as-judge cascade (V2's human review covers
   this at master-CV scale; automation becomes necessary at per-job volume)
7. Application autofill remains V4-ish: Playwright, per-ATS field maps, hard
   human gate before submit

---

## Milestones (re-cut)

**M8 — Schema + extraction.** `evidence`/`claims`/`claim_evidence` migrations.
Two-pass extractor (work statements → Actions; outcome statements → Results)
over GitHub + Drive. PAR validator with the outcome-statement rule.
*Exit test: a fixture commit containing only work statements produces a claim
with `result_kind=missing`, never a filled Result; a fixture commit containing
"reduced X from A to B" produces `quantified` with verbatim metric match.*

**M9 — Review layer.** Claim state machine, review cards with evidence
click-through, edit-with-attestation, rejection reasons, Missing-Results view.
Master CV version = snapshot of approved claims.
*Exit test: an unapproved claim cannot appear in any master CV version; an
edited field carries `user_attestation` provenance.*

**M10 — Interview validation.** Provenance requirements, re-fetch
verification, confirm queue, mode guard, `validation_runs` logging.
*Exit test: fixture email with missing ID or mismatched quote cannot create a
record; valid one lands in confirm queue only.*

**M11 — Rendering.** Template from the real CV, `app/render/` with docxtpl,
approved-claims-only input, style assertions.
*Exit test: rendered Master CV matches template styles exactly; a
`pending_review` claim in the DB does not appear in the output.*

**M12 — Dashboard.** Three queues, master CV download link, job posting links.
*Exit test: full loop on fixtures — extract → review → approve → render →
download, with every bullet click-traceable to evidence.*

---

## Definition of Done (V2.1)

Running on fixtures with zero real credentials:

- Every claim is `approved`, `rejected`, or visibly pending — nothing
  Claude-extracted becomes canonical without human review
- Every approved Result is `quantified` (verbatim metric in evidence),
  `qualitative_evidenced` (verbatim outcome quote in evidence), or
  `user_attested` — and commits/comments were legal sources for all of them
- Master CV docx renders from approved claims only, in the exact format of
  the real CV template
- Fixture interview emails cannot create records without verified provenance
- Dashboard: review queue, missing-results queue, interview confirm queue,
  master CV link, job posting links — full extract→review→render loop works
- The V3 starting line is clean: review-queue decisions accumulating as
  golden-set data, renderer API ready for a tailoring selector, agent kit
  committed and waiting
