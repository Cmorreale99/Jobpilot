# tests/eval — golden-set eval harness

Selects the V2 model stack (bi_encoder / reranker / nli). The full plan —
candidates, fixture schemas, metrics, budgets, safety gates, decision
procedure — lives in `docs/research/MODEL_EVAL_PLAN.md`. This README is the
operational quick reference.

**Status: planned, not implemented.** No inference code exists yet; fixtures
are not yet populated. Nothing in here runs in the normal `pytest -q` suite —
the eval is a manually invoked, model-downloading, minutes-long job and must
never gate CI or the nightly pipeline.

## Layout (target)

```
tests/eval/
  README.md                      # this file
  fixtures/
    golden_claims.jsonl          # frozen PAR-claim corpus (~40-80 claims)
    golden_postings.jsonl        # 15-20 real postings + hand-extracted requirements
    golden_relevance.jsonl       # req_id -> relevant_claim_ids (hand-labeled)
    rerank_candidates.jsonl      # frozen per-requirement candidate lists
    nli_pairs.jsonl              # ~20 entailment pairs, ~half adversarial
  run_eval.py                    # planned entrypoint (not yet written)
  results/                       # per-model result JSON + ADR-ready markdown (git-ignored until final)
```

Fixture line formats are specified in MODEL_EVAL_PLAN.md § Golden-set
fixtures — one JSON object per line, stable string IDs, hand-labeled.

## Rules

1. **Freeze before first run.** Fixtures are committed before any model is
   downloaded or scored, and never edited to improve a number. Label errors
   are fixed only by a commit that says so, after which every model re-runs.
2. **Budgets are pre-registered.** Latency budgets and thresholds are already
   written in the plan; results cannot move them.
3. **Safety gates before metrics.** Pooling, prefix-symmetry, normalization,
   reranker-activation, NLI id2label canary, and truncation checks run
   first; a gate failure invalidates that configuration's metrics (it's a
   harness bug, not a model result).
4. **Pin everything.** Model revisions are pinned by commit hash (MiniLM via
   its renamed repo id `cross-encoder/ms-marco-MiniLM-L6-v2`; the
   nli-deberta tokenizer needs a post-2025-04 snapshot).
5. **No winner outside the harness.** Research narrowed to 2 per slot; only
   these eval numbers select winners, recorded in
   `docs/adr/0001-model-stack.md`.

## Planned invocation (once implemented)

```bash
# One slot, one model, one arm:
uv run python tests/eval/run_eval.py --slot bi_encoder --model BAAI/bge-base-en-v1.5 --arm with-prefix

# Everything, sequentially, writing results/ + the ADR table:
uv run python tests/eval/run_eval.py --all
```

Planned outputs per run: `results/<slug>__<arm>.json` (metrics, score
distributions, latency, gate results, model revision hash) and
`results/summary.md` (the table that pastes into the ADR).

## Candidates under test

| Slot | A | B |
|---|---|---|
| bi_encoder | BAAI/bge-base-en-v1.5 (two arms: ±query prefix) | thenlper/gte-base |
| reranker | BAAI/bge-reranker-base | cross-encoder/ms-marco-MiniLM-L-6-v2 |
| nli | MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli | cross-encoder/nli-deberta-v3-base |

Fallback: bge-small-en-v1.5 (only if both encoders bust the latency budget).
Optional challengers (Qwen3-Embedding-0.6B / Qwen3-Reranker-0.6B) require an
audited fact sheet + constraint check before entering — see the plan's
challenger section.
