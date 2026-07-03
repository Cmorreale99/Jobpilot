# MODEL_EVAL_PLAN — golden-set eval for the JobPilot V2 model stack

Status: **plan only** — no inference code, no fixtures populated, no models
downloaded yet. Source of truth for candidates and risks:
`research/model_comparison.md` (audited fact sheets in `research/models/`).

## Governing rule

Research narrowed each slot to exactly 2 candidates; **this eval — and nothing
else — selects the winners.** Every benchmark number in the comparison matrix
is self-reported, so the golden-set results here are the first independent
measurements in the pipeline and override the matrix by design (open question
7). Results and the decision go into `docs/adr/0001-model-stack.md`.

Two anti-bias rules apply to everything below:

1. **Fixtures freeze before the first model run.** Golden labels are
   hand-written, committed, and never edited afterward to improve a number.
   A labeling *error* may be fixed only via a commit that documents it, and
   all models re-run from scratch.
2. **Budgets and thresholds pre-register.** Latency budgets and any
   pass/fail cutoffs are written into this file *before* results exist, so
   winners can't be rationalized after the fact.

## Candidates

| Slot | Candidate A | Candidate B |
|---|---|---|
| bi_encoder | `BAAI/bge-base-en-v1.5` (CLS pooling, optional query prefix) | `thenlper/gte-base` (mean pooling, no prefixes — symmetric control) |
| reranker | `BAAI/bge-reranker-base` (278M, no English benchmarks published) | `cross-encoder/ms-marco-MiniLM-L-6-v2` (22.7M, documented short-text weakness) |
| nli | `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` (ANLI-robust, 0=entailment) | `cross-encoder/nli-deberta-v3-base` (clean Apache-2.0, 0=contradiction) |

bge-base runs as **two arms**: with and without the query instruction
(`"Represent this sentence for searching relevant passages: "`, queries only,
never on indexed claims) — so the bi-encoder bracket is effectively three
measured configurations (open question 2).

Documented fallback (not a candidate): `bge-small-en-v1.5` (384-dim) enters
only if **both** 768-dim encoders miss the latency/storage budget (open
question 4).

### Optional challenger bracket

- `Qwen/Qwen3-Embedding-0.6B`
- `Qwen/Qwen3-Reranker-0.6B`

Challengers do **not** run in the main bracket. Entry conditions, in order:

1. A scout → auditor fact sheet lands in `research/models/` (same schema,
   same audit pass) — no model enters the eval without audited provenance.
2. The fact sheet passes the same hard-constraint check
   (`research/candidates.yaml`) — notably (a) CPU-friendly and (c) embedding
   dims ≤ 1024. At ~0.6B params these are roughly 5× heavier than anything
   in the current matrix, so **the latency gate runs first**: if a challenger
   blows the pre-registered budget on the target CPU, its quality is never
   measured.
3. A challenger displaces a shortlist winner only by beating it on the same
   frozen golden set *and* fitting the same budget. The golden set is not
   re-labeled for challengers.

## Golden-set fixtures

All fixtures are JSONL (one object per line), hand-labeled, committed under
`tests/eval/fixtures/`, and frozen per the anti-bias rules. IDs are stable
strings; nothing is auto-generated at eval time.

### `golden_claims.jsonl` — the retrieval corpus

A frozen snapshot of real PAR claims from the user's Master CV (a
representative subset, ~40–80 claims). Snapshot, not live: the eval must be
reproducible even after the Master CV re-ingests.

```json
{"claim_id": "clm-0007", "text": "Re-architected the settlement pipeline into idempotent Kafka workers, cutting runtime by 70%", "source_ref": "repo_ref:settler"}
```

### `golden_postings.jsonl` — 15–20 real postings with extracted requirements

Real postings (Remotive or equivalents actually seen by the pipeline), each
with its requirement bullets hand-extracted. `raw_text` is kept in full so
truncation tests (below) run on realistic lengths.

```json
{"posting_id": "post-003", "title": "Staff Backend Engineer, Payments", "company": "Ledgerline", "raw_text": "<full posting text>", "requirements": [{"req_id": "post-003-r1", "text": "5+ years building event-driven backend systems (Kafka or similar)"}]}
```

### `golden_relevance.jsonl` — retrieval/rerank labels

One line per requirement, listing which claims genuinely support it.
Requirements with zero relevant claims are allowed and kept — the pipeline
must handle "no evidence" honestly.

```json
{"req_id": "post-003-r1", "relevant_claim_ids": ["clm-0007", "clm-0012"]}
```

### `nli_pairs.jsonl` — ~20 hand-written entailment pairs

Premise = a real claim; hypothesis = a requirement rephrased as a factual
statement about the candidate. Binary labels (`entailment` /
`not_entailment`), because the pipeline's question is "does this claim
support this requirement", not 3-way NLI. Roughly half the pairs must be
**adversarial near-misses** (right skill, wrong depth/seniority; lexical
overlap without entailment) — this is what decides whether ANLI training
buys real robustness (open question 5).

```json
{"pair_id": "nli-011", "premise": "Mentored three junior engineers through onboarding", "hypothesis": "The candidate has managed a team of engineers", "label": "not_entailment", "adversarial": true}
```

## Metrics

Retrieval task shape: **query = requirement text, corpus = golden claims** —
matching JobPilot V2's direction (find evidence for each requirement).

| Slot | Primary metric | Secondary |
|---|---|---|
| bi_encoder | recall@5 (per requirement, macro-averaged) | recall@10, MRR |
| reranker | precision@3 on a frozen candidate list | MRR, full score distribution |
| nli | accuracy on `nli_pairs.jsonl` | accuracy on the adversarial subset alone; entailment-probability distribution |

Details:

- **Reranker isolation.** Rerankers score a *frozen* candidate list per
  requirement (the union of both encoders' top-10, computed once and
  committed as `fixtures/rerank_candidates.jsonl`), so the reranker
  comparison is independent of which encoder wins.
- **Calibration outputs, not just point metrics** (open question 3): for
  every model, the harness emits the full score distribution over golden
  pairs (relevant vs non-relevant, entailed vs not). Any absolute threshold
  used by V2 matching is derived per-model from these distributions —
  thresholds are never reused across models. Rerankers are normalized with
  an explicit sigmoid (MiniLM ships Identity activation; bge-reranker emits
  unbounded scores).
- **Reporting**: one JSON result file per model+arm under
  `tests/eval/results/`, plus a generated markdown table that pastes into
  the ADR.

## Latency measurement

Environment: the target machine itself (the CPU that runs the nightly
pipeline), no GPU, models in eval mode, single process. Method:

- One untimed warm-up pass per workload, then **3 timed repetitions**;
  report median total wall-clock and per-item p50/p95.
- **Model load time reported separately** from inference (cold start matters
  for the Lambda-portable design).
- Workloads, sized to pipeline scale (open question 4):
  - bi_encoder: embed 250 posting-requirement texts + embed the full golden
    claim corpus.
  - reranker: score 250 (requirement, posting) pairs.
  - nli: score the ~20 golden pairs (NLI runs on TOP_N only in the pipeline,
    so scale is not the concern — per-pair cost is).

**Pre-registered budgets** (set now, before any measurement): the nightly
stage must stay comfortably inside the existing run envelope — embed-250 ≤
120 s, rerank-250 ≤ 300 s, per-NLI-pair ≤ 2 s, all on the target CPU. A model
that busts its budget is eliminated regardless of quality; if both encoders
bust it, `bge-small-en-v1.5` re-enters per the fallback rule.

## Safety tests (gates — run before any metric)

A safety-test failure means a *harness/config bug*, not a model loss; metrics
for that configuration are invalid until it passes. These encode the
silent-breakage risks the fact sheets documented.

1. **Pooling assertion.** The loaded sentence-transformers pooling config
   must match the fact sheet: CLS for bge-base, mean for gte-base. Fail loud
   on mismatch (bge sheets document that mean pooling silently degrades).
2. **Prefix symmetry assertion.** The harness exposes exactly one
   `embed_requirements()` / `embed_claims()` seam per arm; a test asserts
   the configured prefix is applied identically at index time and query time
   for the with-prefix bge arm, and that *no* prefix ever reaches gte or the
   indexed-claims side of bge (open question 2 — silent degradation from
   inconsistent prefixing).
3. **Normalization assertion.** Embedding L2 norms ≈ 1.0 for both encoders.
4. **Reranker activation + repo pin.** Assert sigmoid is applied (all
   reranker scores in [0, 1]) and the MiniLM checkpoint resolves via the
   renamed repo id (`cross-encoder/ms-marco-MiniLM-L6-v2`) with a pinned
   revision — the old id redirects and can break tooling.
5. **NLI id2label canary.** At runtime, read `config.id2label` — never
   hardcode label order (the two candidates order labels *oppositely*).
   Then run canary pairs with unambiguous answers, e.g. premise "A dog is
   sleeping on the couch." / hypothesis "An animal is asleep." → entailment;
   same premise / "The couch is empty." → contradiction. A canary miss
   aborts the eval for that model with a loud error naming the label
   mapping it read.
6. **Truncation / chunking (512-token models — all six candidates).**
   - Report: token count of every `golden_postings.raw_text` under each
     model's own tokenizer, and the count exceeding 512.
   - If any posting exceeds 512 tokens, run the retrieval and rerank
     metrics under two strategies — head-only truncation vs
     chunk-then-max-score — and report both. Requirements are extracted
     bullets and claims are short, so truncation pressure is expected only
     on full-posting inputs.
   - Only a measured, material quality loss from truncation justifies
     re-opening the bi-encoder shortlist for nomic's 8192-token context
     (open question 6). Note: challenger Qwen3-Embedding also carries a
     long context; the same evidence bar applies.
7. **Determinism.** Embedding the same text twice yields identical vectors;
   fixed seeds; `model.eval()` everywhere.

## Decision procedure

1. All safety gates pass for a configuration, else its metrics don't count.
2. Latency budgets eliminate first (pre-registered above).
3. Among survivors, the primary metric decides each slot: recall@5
   (bi_encoder, best arm counts), precision@3 (reranker), accuracy (nli).
4. Tie-breakers, in order: adversarial-subset accuracy (nli) or secondary
   metric (others); then lower latency; then fewer usage requirements
   (fewer ways to silently break in production).
5. One winner per slot + eval results table + harness commit hash go into
   `docs/adr/0001-model-stack.md` (template in the V2 outline). Trade-offs
   accepted and revisit conditions are recorded there, not here.

## Out of scope (for this plan)

- Implementing `run_eval.py` or any model-loading/inference code.
- Populating fixtures (hand-labeling is a human task; formats above).
- Downloading model weights; adding `sentence-transformers`/`torch`
  dependencies (those additions need justification lines in CLAUDE.md when
  the harness is implemented).
- Anything that picks a winner before the harness runs.
