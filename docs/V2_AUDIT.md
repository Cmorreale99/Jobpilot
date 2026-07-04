# Jobpilot v2 Architecture Audit

**Date:** 2026-07-04 · **Branch:** `v2/master-cv` @ `6ba84a0` · **Evidence base:** live `jobpilot.db` (104 claims, 9 experiences, extraction runs 13:54–14:16 UTC)

**Scope:** schemas · prompts · validators · data flow · live output.
**Standard applied:** no bullet without an approved, project-scoped, evidence-backed claim.

> **Root cause.** Jobpilot v2 has **no project/entity boundary layer**. Every downstream guarantee — verbatim grounding, evidence links, the coupling rule, approved-only rendering — is enforced against the wrong unit of scope: *the file*. Two aggravators turn this into slop: raw, unnormalized PDF text feeding a line-splitting fallback extractor, and a second, unvalidated V1 pipeline that still generates the prose that actually leaves the machine.

---

## 1. Executive Diagnosis

**The root cause is architectural: there is no project/entity boundary layer, and everything downstream is scoped to the wrong unit — the file.** `gather_drive_groups` (`app/services/claim_extraction.py:71`) creates one "experience" per Drive document and one per GitHub repo. The live database shows exactly what that does.

**Exhibit A — `jobpilot.db` · `experiences` table (all 9 rows):**

```text
id  name                                                          section
 1  cmorreale_resume.docx (1).pdf                                 professional_experience
 2  Data Systems Case Studies (Wellington, LLM Pipelines,
    Distributed Infra) (2).pdf                                    professional_experience
 3  Cmorreale_2026_finance_CV.pdf                                 professional_experience
 4  Graduate Degree Completion Form_0 (1).pdf                     professional_experience
 5  Cmorreale Resume Sep 2025.docx                                professional_experience
 6  Cam_Morreale_resume.pdf                                       professional_experience
 7  cameron-morreale-portfolio                                    projects_hackathons
 8  AI-Recovery-navigation                                        projects_hackathons
 9  investment-decision-workflow-engine                           projects_hackathons
```

The "experiences" are filenames. Four versions of the same resume became four separate experiences; a degree-completion form became career experience; a document whose title announces it contains three projects became one experience with one 17.6 KB evidence chunk. These names are the section headings the rendered CV would print.

Because "experience" = file, the provenance system is real but semantically empty: a quote is "grounded" if it appears anywhere in a whole multi-job PDF, and a Result "cites its evidence" when the citation is the entire document containing six jobs.

Two aggravating causes turn this from mediocre into slop:

1. **No source normalization.** Real Drive text arrives mangled — word-per-line PDF extraction and run-on docx exports. The heuristic extractor (also the *silent fallback* whenever the LLM call fails, `app/llm/extraction.py:162`) does line-based statement splitting on this text.
2. **A second, unvalidated pipeline still generates prose.** Nightly tailoring and outreach (`app/services/outreach.py:32`) consume the V1 `master_cv` — 133 claims LLM-decomposed from mangled text with no PAR validator, no review, no evidence links. The human-in-the-loop layer gates only the docx render path.

**Exhibit B — `jobpilot.db` · `claims` table, experience 1 (sample):**

```text
claim 1   problem: "Cam Morreale Reverse-engineers broken data systems…"   ← the resume tagline
          action:  "design,"                                               ← one word + comma
claim 2   problem: "~$8M"          action: "Reverse-engineered an unmapped 250-table Oracle…"
claim 4   problem: "Cooper.ai — Data Engineer (Contract) | Remote  June 2026–Present…"
                                                                           ← a job header line
claim 6   problem: "manual"        action: "Designed and implemented a RAG system…"
claim 7   problem: "manual"        action: "extracted"                     ← entire action text

status breakdown: 96/104 missing results · 89/104 validation-flagged · 3 verified results
short problems:   "manual" ×5 · "~$8M" ×2 · "high-risk state" · "“haven't slept in days”"
```

Every reported failure reproduced verbatim: one-word Problems, fragment Actions truncated by PDF line breaks, headers and taglines classified as pain points — and a crisis-text string from an app README extracted as a career Problem.

This is **not a prompting problem**. The prompts are decent and the grounding filters work as written. The system was validated exclusively against fixtures that are pre-labeled `Problem:/Action:/Result:` triplets, one project per tiny file (`tests/fixtures/claims/drive/shipment_reconciliation.md`) — a format zero real documents have. Every test passes; production collapses.

## 2. Failure Taxonomy

| Failure mode | Evidence observed | Likely cause | Severity | Recommended fix |
|---|---|---|---|---|
| Experiences are filenames, not jobs/projects | Exhibit A: 4 resume PDFs = 4 "experiences"; degree form = an "experience"; 3-project case-study PDF = 1 experience | No project/entity detection; `ExperienceSeed(name=document.title)` at `claim_extraction.py:82` | **Critical** | Project roster layer: detect employers/projects, human confirms roster, assign `project_id` before extraction |
| One-word / vague Problems | `'manual'` ×5, `'~$8M'` ×2 in live claims | Problem gate = "cost_dimension or inefficiency non-null" (`par_validation.py:69`), and the heuristic *assigns* those enums by keyword hit (`claims.py:485`) — any line containing "manual" or "$" is a valid Problem | **Critical** | Specificity validator: minimum token count, must not be a substring of the Action, must parse as a clause |
| Result attached to wrong work | Same outcome quote linked to claims 35 *and* 37 (Exhibit C); claim 11 blends the pipeline-failure narrative, the $153B spreadsheet replacement, and a ~40% metric | LLM pass 2 attaches by `claim_index` with grounding checked only against the whole-doc chunk (`extraction.py:201`); heuristic attaches orphan outcomes FIFO to any claim missing a Result (`claims.py:650`) | **Critical** | Result evidence must share `project_id` with the claim; forbid one evidence span supporting more than one claim's Result |
| Prose generated from unvalidated claims | `run_drafting` consumes the V1 `MasterCv` (133 unreviewed claims); `llm/drafting.py` "grounds by claim id" — but the ids index the unvalidated V1 list | V1 pipeline never retired after V2 shipped | **Critical** | Cut tailoring/outreach over to the approved-claims snapshot; delete the V1 structuring path |
| Fragment claims | `action_text='design,'`, `'extracted'`; actions truncated mid-sentence ("…and", "…improving") | Line-splitting on unreflowed PDF text; heuristic fallback runs silently on LLM failure | **High** | Source normalization (reflow, dehyphenate); on real sources fail loudly instead of degrading to the heuristic |
| Review queue flooded | 96/104 claims missing Results, 89/104 flagged (`validation_runs`: 89 fail / 15 pass) | Extractor noise persisted anyway; flags are advisory | **High** | Claims failing *structural* checks never persist; queue only reviewable candidates |
| Duplicate claims across resume versions | Claims 41/42: the same S&P 500 bullet from two files; 4× resume ingestion | `claim_content_fingerprint` dedupes only within one experience against reviewed claims | **High** | Cross-experience content dedupe before queueing; source curation (one canonical resume) |
| Flagged claims approvable as-is | `approve_claim` (`claim_review.py:41`) checks only the state machine; render never rechecks | Review action decoupled from validation verdict | **High** | Approving a flagged claim requires edit-attest or an explicit recorded override |
| V1 "PAR decomposition" invents structure | V1 snapshot claim: the Problem is a *rephrase of the same sentence* as the Action (Exhibit D) | `llm/claim_structurer.py` asks for P/A/R from one sentence; no coupling or content gate | **High** | Retire; V2 extraction is the only claim producer |
| Whole document = one evidence chunk | Drive chunks up to 17.6 KB, READMEs 24 KB; every P/A/R link on a claim cites the same `source_ref` | No sub-document chunking or span offsets for Drive/README sources | **High** | Paragraph/section chunks with char-offset spans; store span on `claim_evidence` |
| Tool detection misses real tools | 75× `action_names_no_tools`; "Snowflake-ready" misses on the `(?![\w-])` boundary; RAG, Power BI, ipywidgets, Seaborn absent from the lexicon | Fixed lexicon + word-boundary regex | Medium | Let the LLM name tools (it does), validate presence in text; lexicon only for the offline path |
| Coupling rule passes coincidental matches | `resolves` derived by keyword lexicon (`derive_resolves`), validated by string membership | Tag equality ≠ semantic relatedness | Medium | Keep as necessary-not-sufficient; add the same-project check, which does the real work |
| Junk sources ingested | Degree completion form; a crisis-text line from the AI-Recovery README extracted as a Problem | Source policy is MIME + folder only; README *content* treated as career evidence | Medium | Source triage step: classify document type before extraction; READMEs contribute Action evidence only unless outcome-bearing |
| Bounce-and-re-extract is a no-op or global | `extract_and_validate_group` pools *all* drafts' violations and re-extracts the whole group once; the heuristic ignores violations by design | Retry designed per-group, not per-claim | Low | Per-claim repair or drop; don't re-prompt with pooled noise |

## 3. Current Pipeline Reconstruction

Two pipelines coexist. The reviewed one feeds the docx; the unreviewed one feeds everything that leaves the machine.

**Path A — V2 claims (feeds review UI and docx only):**

```text
Drive files + GitHub repos
  → policy filter (MIME/folder; username/forks)          [no content triage]
  → EvidenceGroup per FILE/REPO (= "experience")         [boundary error born here]
      Drive: whole doc = 1 chunk; GitHub: README = 1 chunk + 1 chunk/commit
  → two-pass extraction (LLM if flagged on; SILENT heuristic fallback per group)
  → PAR validator → violations pooled → one group re-extraction → persist flagged anyway
  → all claims land pending_review (104 rows, 89 flagged)
  → human review queue (approve / edit-attest / reject)  [no flag gate on approve]
  → approved-claims snapshot → frozen docx renderer
```

- **Gathering** assumes one file = one experience and the file title is a valid resume heading. False for every real input observed.
- **Chunking** assumes a chunk is small enough to be a citation. Drive/README chunks are whole documents; only commits are actually atomic.
- **Extraction** assumes clean, sentence-structured text. Real inputs are mangled PDF text and run-on docx exports. The silent heuristic fallback means one LLM hiccup swaps in a much dumber extractor mid-run with no operator signal — the DB shows both extractors' output coexisting (fragment claims in experience 1, clean paraphrases in experience 5).
- **Validation** is genuinely deterministic and well-tested, but checks *form* (enum non-null, quote-in-chunk, tag membership) against *document-scoped* evidence. Provenance is preserved in the narrow sense — every link resolves to a source — and lost in the meaningful sense: no span, no project.
- **Review** receives the flood: 104 claims, ~4× duplicated, 92% missing results or flagged. The reviewer becomes the chunker, the deduplicator, and the validator — the jobs the pipeline skipped.

**Path B — V1 master CV (feeds matching, tailoring, outreach):**

```text
Drive/GitHub/uploads → cv_sources (raw mangled text)
  → LlmClaimStructurer: P/A/R per document, grounding = quote in whole doc
  → master_cv.content_json (133 claims, zero review, zero validation)
  → keyword-overlap claim selection → highlights / cover letter → outreach queue
```

**Exhibit D — `jobpilot.db` · `master_cv` v1 · first of 133 unreviewed claims:**

```text
problem: "Fragmented legacy Excel workflow powering a $153B investment platform
          used by ~160 investment professionals"
action:  "Reconstructed the data architecture powering a $153B investment platform
          used by ~160 investment professionals, replacing a fragmented legacy
          Excel workflow"                        ← the Problem is the Action, restated
result:  "Driving ~$8M in annual efficiency gains"
evidence_text: "Reconstructed  the  data  architecture … replacing\n \na\n \nfragmented\n
          \nlegacy\n \nExcel\n \nworkflow …"     ← word-per-line PDF extraction, unnormalized
```

Path B has no state machine, no validator, and no review. Its P/A/R triplets are decompositions of single resume sentences — which is why the output reads as "polished but unsupported."

## 4. Project Boundary Analysis

- **Can a Result from Project A contaminate Project B?** Yes, and it demonstrably did. Within any evidence group, the LLM attaches Results by `claim_index`, and grounding only checks that the quote exists in the cited chunk — which for Drive is the whole multi-project document. The heuristic's orphan-outcome FIFO (`claims.py:650`) attaches leftover outcomes to *any* claim missing a Result. Problems contaminate too: the heuristic's most-recent-problem carry-forward attached the `Cooper.ai` job header as the Problem for subsequent unrelated bullets.
- **Where does that become possible?** The moment an `EvidenceGroup` is constructed from a multi-project file. Everything after that is scoped to the group, so nothing downstream can detect the blend.
- **Is project_id assigned early enough?** There is no `project_id`. `experience_id` exists and is assigned early — but it identifies a *file*, so early assignment binds claims to the wrong entity with full confidence.
- **Are source chunks traceable to project, source, and evidence span?** Source: yes. Project: no. Span: no — `claim_evidence` stores a quote but no offsets, and for Drive the chunk *is* the document, so the "span" is 17 KB wide.
- **Does the generator have guardrails against cross-project blending?** No. `render_claim_bullet` and the snapshot builder trust `experience_id` grouping absolutely. The bullets are faithful to the claims; the claims are unfaithful to the projects.

**Exhibit C — one outcome quote supporting two different claims:**

```text
outcome_quote: "reducing manual reporting time by 40% and enabling leadership to
                prioritize high-risk sites, optimize shipment schedules, …"
  ↳ claim 35   action: "Developed an NLP pipeline using a generator-discriminator model…"
  ↳ claim 37   action: "Integrated Power BI dashboards to visualize PFAS contamination…"
```

The same 40% metric now backs two different pieces of work. A rendered CV would print the identical impact statement under two bullets.

## 5. RAG / Retrieval Analysis

There is currently **no retrieval layer at all** — extraction is full-document context dumping, and tailoring selects claims by keyword overlap.

- **Document-centered vs. claim-centered:** extraction is document-centered (whole files into the prompt). Tailoring is claim-centered in shape (selects claims, LLM drafting references claim ids) — but over the *unvalidated V1* claim list, so the good shape sits on the bad data.
- **Project-scoped or globally fuzzy:** file-scoped, which is worse than globally fuzzy because it *looks* scoped.
- **Evidence-preserving or context-dumping:** context-dumping at extraction; evidence-preserving in the V2 schema (quotes, links) but at document granularity.
- **Safe enough for resume generation:** no — not because retrieval is weak, but because the corpus has no project spine to retrieve against.

**Does Jobpilot need RAG?** Not vector-database RAG. The corpus is tiny (6 documents, 59 commits, one user). Embedding retrieval would add infrastructure and fuzziness while solving nothing: the failure is boundary assignment, not recall. What it needs is the "good RAG" definition with retrieval replaced by *deterministic project-scoped lookup*: project-scoped evidence → atomic claims → validation → approval → generation from approved claims only. The claim ledger — which V2 already half-built — *is* the retrieval index. Tailoring should query `approved claims WHERE project_id IN (…)` ranked by overlap with the job. No embeddings until the approved-claim count makes keyword ranking visibly fail.

## 6. PAR Schema Analysis

The schema (`claims` table + `DraftClaim`) is genuinely better than most: enums for cost/inefficiency, `result_kind`/`result_status` separation, `resolves` coupling, `outcome_quote` required on result links, and a documented no-CHECK-constraint decision. Against the checklist:

| Defect | Prevented? | Why |
|---|---|---|
| One-word Problems | **No** | Gate is enum-non-null; enums are keyword-derived from the same text. `'manual'` passes. |
| Generic Problems | **No** | No specificity requirement; a resume tagline containing "broken" became a Problem. |
| Missing Results | **Yes** | `result_kind=missing` modeled honestly; never filled. This part works. |
| Unsupported Results | **Partly** | `outcome_quote`/`metric_text` must be verbatim in the cited chunk — but the chunk is a whole document, and `result_text` itself is an unchecked paraphrase (claim 32's `result_text` adds "adopted by ~160 professionals worldwide" framing beyond its metric quote). |
| Results from other projects | **No** | No project on the claim; chunk-level citation can't distinguish. |
| Metrics with no evidence | **Mostly** | Quantified metric grounding is enforced and works. Weakest link is the document-wide grounding scope. |
| Actions disguised as Results | **Mostly** | The outcome-statement rule and the pass-2 prompt handle this well; the heuristic's `_is_outcome` is reasonable. |
| Vague business impact | **No** | "improving transparency and usability" fragments pass as qualitative results if quoted verbatim. |

**Schema additions needed:** a `projects` entity (or make `experiences` real: `kind: employer_role | project`, canonical name, dates, aliases); `claims.project_id NOT NULL`; `claim_evidence.span_start/span_end`; `evidence.project_id` (nullable until assigned); a `problem_text` specificity contract enforced in the validator, not the DB; and a uniqueness rule that one result evidence span supports at most one claim.

## 7. Claim Ledger Recommendation

Jobpilot needs the ledger it already 80% built — corrected, not replaced. The missing entities are `Project` and `EvidenceSpan`. Minimal target schema:

```jsonc
// project — the boundary everything scopes to; human-confirmed
{ "id": 11, "user_id": "u1", "kind": "employer_role",       // or "project"
  "name": "Wellington Management — Technology Intern",
  "dates": "Jun–Aug 2025", "status": "confirmed",           // proposed | confirmed | merged
  "aliases": ["Wellington", "$153B investment platform"] }

// source — one artifact (exists today as cv_sources / evidence roots)
{ "id": 3, "source_type": "drive", "source_ref": "1VzqS9...",
  "title": "Data Systems Case Studies.pdf", "normalized_text_sha": "..." }

// chunk — paragraph/section-sized, project-assigned
{ "id": 87, "source_id": 3, "project_id": 11,
  "char_start": 1204, "char_end": 1688,
  "text": "While tracing execution paths, ..." }

// claim — as today, plus project scope
{ "id": 35, "project_id": 11, "status": "pending_review",
  "problem": { "text": "...", "cost_dimension": "time", "inefficiency": "manual" },
  "action":  { "text": "...", "tools": ["Python", "Power BI"] },
  "result":  { "kind": "quantified", "status": "verified",
               "text": "...", "metric": { "metric_text": "40%", "resolves": "time" } } }

// evidence link — span-level; one result-span → one claim
{ "claim_id": 35, "chunk_id": 87, "field": "result",
  "quote": "reducing manual reporting time by 40%",
  "quote_start": 1310, "quote_end": 1349 }

// attestation — exists today (user_attestation evidence); keep it
{ "claim_id": 40, "field": "result",
  "attested_text": "Cut onboarding from 3 days to 1",
  "attested_at": "2026-07-04T14:30:00Z" }

// resume bullet — rendered artifact, traceable
{ "bullet": "Automated PFAS reporting with Power BI, reducing manual reporting time by 40%.",
  "claim_ids": [35], "master_cv_version": 3 }
```

The claim state machine, edit-attestation provenance, rejection-with-reason retention, and fingerprint-idempotent snapshots are all correct as built. Keep them.

## 8. Human-in-the-Loop Redesign

Review currently happens at exactly one altitude — per-claim, after extraction — and therefore too late: boundaries are already wrong, and the queue is already flooded with 104 items, 4× duplicates, 89 flagged. The reviewer is being used as a garbage filter, which is why adding a human-in-the-loop element didn't fix quality: **no amount of per-claim review fixes claims scoped to the wrong project.**

Review should happen at three points, cheapest first:

1. **After source ingestion (new, tiny).** "6 Drive files found. 4 look like versions of the same resume — which is canonical? `Graduate Degree Completion Form.pdf` doesn't look like career evidence — include?" One-time, ~30 seconds, removes ~60% of downstream noise.
2. **After project detection (new, the load-bearing one).** Show the proposed roster — "Wellington (Tech Intern, 2025); Cooper.ai (Data Engineer, 2026–); PFAS/environmental NLP project; AI-Recovery-navigation; …" with merge / split / rename / discard. Confirming ~10 entities takes two minutes and makes every downstream claim scoped correctly.
3. **After claim extraction + validation (exists — keep, but gate and target it).** Only structurally sound claims enter the queue; flagged claims cannot be approved as-is; and the card asks the *specific* missing question rather than displaying a validator string:
   - Missing result: *"Project: Wellington platform rebuild. This claim has no observable Result. What changed after this shipped? (Leave blank to render action-only.)"*
   - Coupling flag: *"The Problem is about **quality** (pipeline failures) but the Result is about **manual effort** (~40% reporting time). Same piece of work, or two claims?"* — with a split action.
   - Suspected duplicate: *"This looks like the same accomplishment as claim #41 from a different resume version. Merge?"*

Pre-render review of the assembled CV is worth keeping as a glance (section order, headings), but if steps 1–3 are done it stops being a correctness gate.

## 9. Anti-Slop Validators

All deterministic or cheap; add to `par_validation.py` plus a new pre-persistence structural gate.

- **Problem completeness:** ≥ 6 tokens AND contains a finite verb or explicit cost phrase; `problem_text` must not be a substring of `action_text` (kills the V1 restatement pattern); must not match resume-artifact patterns (`\w+@\w+`, date-range headers, `\.pdf|\.docx`, phone numbers). *Rejects `'manual'`, `'~$8M'`, the Cooper.ai header, the tagline.*
- **Action specificity:** ≥ 5 tokens; must not end mid-clause (terminal `,`, "and", "with", "to"); tools named must appear in text (exists) with hyphen-tolerant matching ("Snowflake-ready"). *Rejects `'design,'`, `'extracted'`.*
- **Result support:** result link required (exists); *new:* result chunk's `project_id == claim.project_id`; *new:* one result span links to ≤ 1 claim; *new:* `result_text` must be entailed by its quotes — deterministic version: every number/percent/currency token in `result_text` appears in a cited quote.
- **Metric support:** verbatim metric (exists) + the number-token subset check above, so a paraphrased `result_text` can't smuggle "~160 professionals worldwide" past a "40%" quote.
- **Project consistency:** every evidence link on a claim resolves to chunks of the same project; cross-project citation requires an explicit `cross_project: true` human confirmation.
- **Evidence sufficiency:** a chunk cited as evidence must be ≤ ~1,200 chars (citation-sized); document-sized chunks are ineligible as citations.
- **Bullet factuality:** at render time, every bullet must decompose into `claim_ids` of approved claims; every number in the bullet appears in one of those claims' evidence quotes or attestations. Fail closed — drop the bullet, log.
- **Tailoring safety:** highlights and cover-letter lines must reference approved claim ids (drop hallucinated ids — exists in `llm/drafting.py`, but point it at the approved ledger); zero tolerance for numbers not present in the referenced claims; contacts from research or honestly absent (exists).

## 10. Recommended v3 Architecture

The target architecture is right, with one adjustment: replace the generic "retrieval/provenance layer" with deterministic project-scoped lookup over the ledger (no vector store), and add the source-triage review up front.

```text
sources (curated: 1 canonical resume, case-study docs, repos, commits)
→ source normalization        (PDF reflow/dehyphenation, docx sectioning, README cleanup)
→ source triage               [HUMAN: keep/discard, canonical-resume pick]
→ project/entity detection    (LLM proposes roster from all sources)
→ project roster confirmation [HUMAN: merge/split/rename — the load-bearing review]
→ project-scoped chunking     (paragraph/section chunks with char spans, project_id)
→ atomic claim extraction     (two-pass, per project — the existing extractor, smaller inputs)
→ deterministic validation    (existing PAR gates + §9 validators; structural failures never persist)
→ targeted human review       (missing/weak/contradictory fields only; flags block approve-as-is)
→ approved claim ledger       (existing state machine, edits, attestations — unchanged)
→ master CV = snapshot of approved claims (exists)
→ rendering + tailoring + outreach FROM APPROVED CLAIMS ONLY (kill Path B)
```

Most of the boxes already exist and are well-built. v3 is three new boxes (normalization, triage, project roster), one scope fix (chunking/citation granularity), and one deletion (the V1 structuring path).

## 11. Implementation Plan

### Phase 1 — Stop the bleeding (days)

- **Files:** `app/services/claim_extraction.py`, `app/domain/par_validation.py`, `app/llm/extraction.py`, `app/services/claim_review.py`, Drive folder contents.
- **Change:** curate sources (one canonical resume; remove the degree form and stale versions). Add text normalization (whitespace reflow, dehyphenation) at ingestion. Add the Problem/Action structural validators — failing claims are *dropped*, not queued. Make LLM-extraction failure on real sources loud (log + flag the group) instead of silently degrading to the heuristic. Block approve-as-is on flagged claims. Cross-experience claim dedupe before queueing.
- **Test:** re-run `python -m app.tools.run_claim_extraction`; assert zero one-word problems, zero fragment actions, queue drops from 104 to a reviewable ~20–30.
- **Impact:** the queue becomes usable; the worst slop becomes unrepresentable. Boundaries are still wrong.

### Phase 2 — Structural fix (the real work, 1–2 weeks)

- **Files:** new `app/domain/projects.py`; migration `0010` (projects, chunk spans, `claims.project_id`); `claim_extraction.py` (chunking + grouping rewrite); `app/llm/` (roster-proposal prompt); `app/api/claims.py` + `web/` (roster confirmation UI); `par_validation.py` (project-consistency + span checks).
- **Change:** project roster detection over normalized sources → human confirmation → paragraph-level project-assigned chunks → extraction per project → result evidence constrained to the same project.
- **Test:** a fixture mirroring reality — one multi-project PDF with mangled spacing + four near-duplicate resumes; assert claims land under confirmed projects, no cross-project result links, no duplicate outcome spans.
- **Impact:** eliminates the root cause; the rendered CV gets real headings (employers and projects, not filenames).

### Phase 3 — Tailoring from the ledger (days)

- **Files:** `app/services/outreach.py`, `app/services/pipeline.py`, `app/domain/tailoring.py`, `app/llm/drafting.py`; delete `app/llm/claim_structurer.py` and the V1 builder path in `master_cv_ingestion.py`.
- **Change:** `run_drafting` reads the approved-claims snapshot; highlights carry `claim_ids`; bullet-factuality validator on output.
- **Test:** extend `test_full_v2_loop.py` — outreach drafted with zero approved claims must contain zero experience content; every highlight traces to an approved claim id.
- **Impact:** closes the "no prose before validated claims" invariant on the path that actually leaves the machine.

### Phase 4 — Evaluation harness (ongoing)

- **Files:** `tests/fixtures/real_world/` (anonymized mangled-PDF fixtures); new `app/tools/eval_extraction.py`.
- **Change:** golden set from retained approve/reject decisions (already stored — the design anticipated this). Slop metrics per run: fragment rate, duplicate rate, missing-result rate, flag rate, cross-project-link count (must be 0). Track per extraction run in `validation_runs`.
- **Impact:** regression detection; extractor or prompt changes get a scorecard instead of vibes.

## 12. Test Cases

```python
# 1. One-word problem is rejected structurally, never persisted
def test_one_word_problem_dropped():
    draft = make_draft(problem_text="manual", problem_inefficiency=Inefficiency.MANUAL)
    assert any(v.code == "problem_not_specific" for v in validate_claim(draft))

# 2. Missing result stays missing through render
def test_missing_result_renders_action_only():
    claim = approved_claim(result_kind=ResultKind.MISSING)
    assert render_claim_bullet(claim) == _sentence(claim.action_text)  # no invented impact

# 3. Result from a different project is rejected
def test_cross_project_result_link_rejected():
    claim = draft_with(action_chunk=chunk(project_id=1),
                       result_chunk=chunk(project_id=2, text="cut costs by 30%"))
    assert any(v.code == "result_project_mismatch" for v in validate_claim(claim))

# 4. Invented metric: number in result_text absent from evidence quotes
def test_metric_not_in_quotes_rejected():
    claim = draft_with(result_text="adopted by 500 users, cutting effort 40%",
                       outcome_quote="cutting effort 40%")   # "500" unsupported
    assert any(v.code == "result_number_unsupported" for v in validate_claim(claim))

# 5. Multi-project document is split before extraction
def test_multi_project_doc_yields_multiple_projects():
    roster = detect_projects([fixture("case_studies_wellington_llm_infra.pdf")])
    assert len(roster) >= 3          # not one experience named after the filename

# 6. Mangled PDF text (word-per-line) produces zero fragment claims
def test_mangled_pdf_no_fragment_actions():
    claims = extract(normalize(fixture("word_per_line_resume.txt")))
    assert all(len(c.action_text.split()) >= 5 for c in claims)
    assert not any(c.action_text.rstrip().endswith((",", "and", "with")) for c in claims)

# 7. User attestation resolves a missing result with provenance
def test_attested_result_traceable():
    claim = edit_and_approve(missing_result_claim,
                             ClaimEdits(result_text="Cut onboarding from 3 days to 1",
                                        result_kind=ResultKind.QUALITATIVE_EVIDENCED))
    assert claim.result_status is ResultStatus.USER_ATTESTED
    assert any(l.field is ClaimField.RESULT and is_attestation(l) for l in claim.evidence)

# 8. Generator refuses unapproved claims
def test_render_and_tailor_reject_unapproved():
    ctx = build_resume_context(profile, [exp], [pending_claim, rejected_claim])
    assert ctx["experiences"] == [] and ctx["projects_and_hackathons"] == []
    materials = run_drafting(deps_with_only_pending_claims)
    assert all(h.claim_ids and all(is_approved(i) for i in h.claim_ids)
               for h in materials.highlights)

# 9. One outcome span cannot support two claims (regression for claims 35/37)
def test_outcome_span_unique_per_claim():
    storables = extract_and_validate_group(extractor, group_with_one_outcome_two_actions)
    result_links = [l for s in storables for l in s.draft.evidence
                    if l.field is ClaimField.RESULT]
    assert len({(l.chunk.source_ref, l.outcome_quote) for l in result_links}) \
        == len(result_links)
```

## 13. Final Verdict

- **Prompting, architecture, data quality, or evaluation problem?** **Architecture, with a data-quality trigger.** The mangled sources exposed it, but a sound architecture is *supposed* to survive ugly inputs — this one converts them into confident structured slop because its unit of scope is the file and its fallback path is a line-splitter. It is also partly an evaluation problem: every test runs on pre-labeled `Problem:/Action:/Result:` fixtures that no real document resembles, so the suite is green while production is garbage. It is *not* a prompting problem — the prompts and grounding filters are among the best-built parts.
- **Does Jobpilot v2 need RAG?** No vector RAG. It needs the second half of the good-RAG definition: project-scoped evidence → validated atomic claims → approved ledger → generation from the ledger only. The ledger *is* the retrieval layer; at one user and a few dozen approved claims, deterministic project-scoped lookup with keyword ranking beats embeddings on every axis that matters here.
- **Single highest-leverage fix?** The **project/entity boundary layer with a human-confirmed roster, assigned before extraction** (source normalization as its prerequisite). Every observed failure — one-word problems from headers, cross-project results, filename headings, duplicate claims, the flooded review queue — either disappears or becomes detectable once claims are scoped to confirmed projects.
- **What should not be built yet?** Vector databases and embeddings, an LLM-judge validation layer, the RL/self-updating layer (correctly already out of scope), more prompt iteration, and any new LLM capability on the tailoring side. Also stop *maintaining* something: the V1 structuring path should be deleted, not improved — as long as it feeds outreach, the review layer is security theater for the output that matters most.

---

**Credit where due:** the V2 skeleton — claim state machine, verbatim grounding, attestation provenance, missing-never-invented results, retained review decisions — is the right design and mostly well-executed. v2 didn't fail because the ideas were wrong; it failed because the ledger was bolted onto file-shaped boundaries, fed unnormalized text, and bypassed by the legacy path on the way out the door.
