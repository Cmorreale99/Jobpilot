# Model comparison — JobPilot V2 model stack

Synthesized exclusively from the audited fact sheets in `research/models/` and
`research/decision_criteria.md`. Every cell traces to a fact-sheet field;
`?` means the sheet lists it as unknown. **No winner is picked here** — per the
governing rule, winners come only from the golden-set eval (`tests/eval/`).

Note on benchmarks: **every benchmark score in every fact sheet is
`self_reported: true`** (all trace to model cards / author docs; no independent
leaderboard verification was fetchable during audit). Treat all numbers below
as author-reported and not cross-comparable across model families.

---

## 1. Comparison tables

### Slot: `bi_encoder`

| Model | Params (M) | Dims | Max seq len | Key benchmark (self_reported?) | License | Usage requirements (condensed) | Known issues (condensed) |
|---|---|---|---|---|---|---|---|
| BAAI/bge-small-en-v1.5 | 33.4 | 384 | 512 | MTEB avg 62.17; MTEB Retrieval nDCG@10 51.68 (self_reported: yes) | MIT | CLS pooling (NOT mean); L2-normalize; optional query-only instruction prefix; no trust_remote_code; English-only | Uncalibrated absolute scores (rank-only); asymmetric prefix easy to misapply — inconsistent indexing/query prefixing degrades silently |
| BAAI/bge-base-en-v1.5 | 109.48 | 768 | 512 | MTEB avg 63.55; MTEB Retrieval (BEIR-15) nDCG@10 53.25 (self_reported: yes) | MIT | CLS pooling (NOT mean); L2-normalize; query prefix optional in v1.5 (queries only, never passages); no trust_remote_code; English-only | Scores compressed to ~[0.6, 1.0] (temp 0.01) — >0.5 does not imply similarity; any absolute threshold must be calibrated (typ. 0.8–0.9); mean pooling silently degrades |
| thenlper/gte-base | 110 | 768 | 512 | MTEB avg 62.39; MTEB Retrieval 51.14; MTEB STS 82.3 (self_reported: yes) | MIT | **No prefixes** (symmetric); MEAN pooling (NOT CLS); L2-normalize; no trust_remote_code; English-only | Cosine scores compressed high (~0.7–0.8 for unrelated text) — relative ranking only; silent 512-token truncation; do not confuse with Alibaba gte-*-v1.5 (custom arch) |
| intfloat/e5-base-v2 | ? | 768 | 512 | BEIR avg nDCG@10 50.3; no aggregate MTEB avg published (self_reported: yes) | MIT | **Mandatory** `query: `/`passage: ` prefixes on EVERY input (`query: ` both sides for symmetric tasks); MEAN pooling; L2-normalize; no trust_remote_code; English-only | Omitting prefixes degrades silently (no error); scores compressed ~0.7–1.0 — no absolute thresholds; params count not published |
| nomic-ai/nomic-embed-text-v1.5 | 137 | 768 (Matryoshka: 512/256/128/64) | 8192 (2048 native; rope scaling beyond) | MTEB avg 62.28 @768d; 61.96 @512d (self_reported: yes) | Apache-2.0 | **Mandatory** task prefixes (`search_query: `, `search_document: `, etc.); mean pooling; layer-norm before Matryoshka truncation; trust_remote_code=True on transformers <5.5.0 / s-t <5.3.0; einops dep on that path | External-repo modeling files break offline/serialized loads (HF #12, #25); prefix handling is caller responsibility; serving-layer trust_remote_code plumbing issues; heaviest of the slot |

### Slot: `reranker`

| Model | Params (M) | Dims | Max seq len | Key benchmark (self_reported?) | License | Usage requirements (condensed) | Known issues (condensed) |
|---|---|---|---|---|---|---|---|
| BAAI/bge-reranker-base | 278.04 | n/a (scalar score) | 512 per pair | C-MTEB T2Reranking map 67.28 — **Chinese-domain benchmarks only** (self_reported: yes) | MIT | (query, passage) pairs → scalar; no prefixes; raw scores unbounded — sigmoid/`normalize=True` for [0,1]; no trust_remote_code; Chinese + English only | Recurring score-range confusion (FlagEmbedding #965); prior generation — BAAI points to bge-reranker-v2 series; cross-encoder cost: top-k rerank only |
| cross-encoder/ms-marco-MiniLM-L-6-v2 | 22.7 | n/a (scalar score) | 512 per pair | TREC DL 19 NDCG@10 74.3; MS MARCO Dev MRR@10 39.01 (self_reported: yes) | Apache-2.0 | Raw (query, passage) pairs; default activation Identity → raw logits ~[-10, 10], pass Sigmoid for 0–1; no trust_remote_code; pin new repo id `ms-marco-MiniLM-L6-v2` | Repo renamed (redirect can break tooling); **reported low scores on similar short texts outside MS MARCO distribution** (s-t #2874); silent >512-token truncation; English-only; training hyperparams undocumented |

### Slot: `nli`

| Model | Params (M) | Dims | Max seq len | Key benchmark (self_reported?) | License | Usage requirements (condensed) | Known issues (condensed) |
|---|---|---|---|---|---|---|---|
| MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli | 184.4 | n/a (class logits) | 512 | MNLI-m acc 0.903; ANLI-all 0.579 (self_reported: yes) | MIT (see caveat) | (premise, hypothesis) pairs; **label order 0=entailment, 1=neutral, 2=contradiction**; needs sentencepiece; transformers >= 4.13; fp16 checkpoint; no trust_remote_code; zero-shot pipeline compatible | **Possible license inconsistency**: MIT-declared but some training data (ANLI components) is CC BY-NC 4.0 — flagged in HF discussions; 512-token truncation |
| cross-encoder/nli-deberta-v3-base | 184 | n/a (class logits) | 512 | SNLI test acc 92.38; MNLI-mm acc 90.04 (self_reported: yes) | Apache-2.0 | (premise, hypothesis) pairs; **label order 0=contradiction, 1=entailment, 2=neutral** — differs from the model above; needs sentencepiece; no trust_remote_code; zero-shot pipeline compatible | History of DeBERTa-v3 tokenizer instantiation failures (transformers #14470); maintainer re-pushed tokenizer files Apr 2025 — use a recent snapshot; MNLI-matched acc unknown (?) |

---

## 2. Constraint check (hard constraints from candidates.yaml)

Constraints: (a) CPU-friendly local inference; (b) license permits
commercial/personal automated use; (c) bi-encoder dims <= 1024; (d)
English-only acceptable; (e) actively maintained or stable/widely adopted.

| Model | (a) CPU | (b) License | (c) Dims | (d) English | (e) Maintained | Verdict |
|---|---|---|---|---|---|---|
| bge-small-en-v1.5 | 33.4M — pass | MIT — pass | 384 — pass | pass | 61.8M dl/mo — pass | QUALIFIED |
| bge-base-en-v1.5 | 109.5M — pass | MIT — pass | 768 — pass | pass | 8.25M dl/mo — pass | QUALIFIED |
| gte-base | 110M — pass | MIT — pass | 768 — pass | pass | 439K dl/mo — pass | QUALIFIED |
| e5-base-v2 | ? params (12-layer BERT-base class) — pass | MIT — pass | 768 — pass | pass | 2.07M dl/mo — pass | QUALIFIED |
| nomic-embed-text-v1.5 | 137M — pass (heaviest bi-encoder) | Apache-2.0 — pass | 768 (≤1024) — pass | pass | 16.9M dl/mo — pass | QUALIFIED (caveat: trust_remote_code + external-repo files on older stacks conflicts with a fully offline/pinned install; not a listed hard constraint) |
| bge-reranker-base | 278M — pass, but largest model in the whole matrix; sheet notes cross-encoder cost and fp16 recommendation | MIT — pass | n/a | CN+EN, English supported — pass | 4.32M dl/mo — pass (caveat: prior generation per BAAI) | QUALIFIED |
| ms-marco-MiniLM-L-6-v2 | 22.7M — pass (lightest) | Apache-2.0 — pass | n/a | pass | 80.4M dl/mo — pass | QUALIFIED |
| MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli | 184.4M — pass | MIT declared, but sheet flags possible CC BY-NC 4.0 taint from ANLI training data. JobPilot is single-user personal automation, so constraint (b) "commercial/**personal** automated use" is satisfied on the declared MIT license — **flag for human review** if this project ever becomes commercial | n/a | pass | 119K dl/mo — pass | QUALIFIED (with license caveat) |
| cross-encoder/nli-deberta-v3-base | 184M — pass | Apache-2.0 — pass | n/a | pass | 378K dl/mo, tokenizer re-pushed 2025-04 — pass | QUALIFIED |

**No model violates a hard constraint → no DISQUALIFIED entries.** All nine
proceed to shortlisting on fact-sheet evidence.

---

## 3. Shortlist (exactly 2 per slot)

Shortlisting narrows the field per the governing rule; it does **not** pick
winners. Ties are broken by the golden-set eval only.

### bi_encoder

**1. BAAI/bge-base-en-v1.5** (`baai-bge-base-en-v1.5.json`)
It carries the highest self-reported scores in the slot (MTEB avg 63.55,
BEIR retrieval nDCG@10 53.25) on a standard BERT architecture with no
trust_remote_code, MIT license, and 768 dims well inside the pgvector limit.
Its v1.5 revision makes the query instruction optional, so the eval harness
can test both symmetric and asymmetric configurations from one checkpoint.
The main documented risk — similarity scores compressed into ~[0.6, 1.0] —
is shared by the whole slot and is a calibration task for the eval, not a
disqualifier.

**2. thenlper/gte-base** (`thenlper-gte-base.json`)
It is the only candidate with a fully symmetric usage contract — no
query/passage prefixes at all — which eliminates the silent
prefix-mismatch failure class that the bge, e5, and nomic sheets all
document as an easy-to-get-wrong pipeline hazard. Its self-reported MTEB STS
score (82.3) is the strongest listed in the slot, relevant because
claim-to-requirement matching is closer to symmetric similarity than to
short-query/long-passage retrieval. MIT-licensed, vanilla BERT, ~110M params
— same CPU class as bge-base, giving the eval a clean prefix-vs-no-prefix
A/B at equal size.

*Not shortlisted:* **bge-small-en-v1.5** — same family, same usage contract,
lower self-reported scores than bge-base on every listed subscore; it adds no
independent signal, though it remains the natural 384-dim fallback if 768-dim
CPU latency fails the eval budget (see Open Questions). **e5-base-v2** —
mandatory prefixes on *every* input with documented silent degradation, no
published aggregate MTEB score to weigh against the others, and an unknown
param count. **nomic-embed-text-v1.5** — its differentiators (8192-token
context, Matryoshka dims) don't map to short PAR claims and requirement
bullets, while it brings the slot's heaviest weights plus documented
trust_remote_code/external-repo loading fragility for offline or pinned
installs.

### reranker

**1. BAAI/bge-reranker-base** (`baai-bge-reranker-base.json`)
MIT-licensed cross-encoder purpose-built for reranking a top-k shortlist,
with a documented normalization path (sigmoid / `normalize=True`) to [0,1]
scores and no prefix requirements. Caveats from the sheet: all its published
benchmarks are Chinese-domain C-MTEB numbers, so its English reranking
quality is effectively unmeasured until the golden-set eval, and at 278M
params it is the heaviest model in the matrix — CPU latency must be measured,
not assumed. It advances because it is the only candidate in the slot not
carrying the short-text distribution concern documented against MiniLM.

**2. cross-encoder/ms-marco-MiniLM-L-6-v2** (`cross-encoder-ms-marco-minilm-l-6-v2.json`)
At 22.7M params it is the lightest model in the entire matrix — the most
CPU-friendly reranker by an order of magnitude (~12x fewer params than
bge-reranker-base) — with Apache-2.0 license and strong self-reported English
passage-ranking numbers (TREC DL 19 NDCG@10 74.3). The sheet's key documented
risk is directly relevant here: community reports of low scores on fairly
similar short texts outside the MS MARCO query-passage distribution
(sentence-transformers #2874), which is exactly JobPilot's claim/requirement
shape. That makes it a mandatory eval candidate rather than a paper winner
or a paper loser.

### nli

**1. MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli** (`moritzlaurer-deberta-v3-base-mnli-fever-anli.json`)
Trained on MNLI + FEVER-NLI + ANLI, it is the only candidate with published
adversarial-robustness numbers (ANLI-all 0.579), which matters when checking
whether a tailored claim genuinely entails a job requirement rather than
merely overlapping lexically. Self-reported MNLI-m accuracy (0.903) is on
par with the alternative, on the same DeBERTa-v3-base backbone (~184M params,
CPU-equivalent). Two caveats ride along: the MIT-vs-ANLI-CC-BY-NC license
flag (acceptable for personal use, needs human review for anything
commercial) and a label order (0=entailment) opposite to its rival's.

**2. cross-encoder/nli-deberta-v3-base** (`cross-encoder-nli-deberta-v3-base.json`)
Clean Apache-2.0 license with no training-data flags, the same
DeBERTa-v3-base backbone, and the slot's highest self-reported clean-set
numbers (SNLI 92.38, MNLI-mm 90.04). It lacks any published
adversarial (ANLI-type) results, so its robustness on hard
claim-vs-requirement pairs is unknown until the eval. Sheet-documented
integration risks are operational, not architectural: sentencepiece is
required, and the repo's tokenizer files were re-pushed in April 2025, so
the harness must pin a recent snapshot.

*(Both slots with only two candidates advance both — the constraint check
disqualified neither.)*

---

## 4. Open questions for the eval harness

These are the questions the golden-set eval (`tests/eval/`) must answer;
research cannot.

1. **Short-text performance on PAR claims.** Every benchmark in every sheet
   measures query-passage or sentence-pair distributions unlike ours: PAR
   claims and requirement bullets are short, dense, first-person fragments.
   The MiniLM sheet explicitly documents degraded scores on similar short
   texts outside MS MARCO (s-t #2874), and bge-reranker-base has *no*
   English benchmark at all. The golden set must include real claim ↔
   requirement pairs and score nDCG/recall on them directly.

2. **Prefix handling under docxtpl pipeline text.** For bge-base the query
   instruction is optional; the eval must run it both with and without
   `"Represent this sentence for searching relevant passages: "` (queries
   only, never on indexed claims) and record which wins on the golden set —
   and the harness should assert that whatever text passes through the
   tailoring/docxtpl path is embedded with the *same* prefixing as at index
   time, since the bge and e5 sheets both document silent degradation from
   inconsistent prefixing. gte-base needs a matching no-prefix arm as the
   symmetric control.

3. **Similarity-score calibration.** All shortlisted bi-encoders compress
   cosine scores into a high band (bge ~[0.6, 1.0]; gte ~0.7–0.8 even for
   unrelated text). If matching applies any absolute threshold (e.g. a
   minimum-fit cutoff before shortlisting), the eval must derive per-model
   thresholds from golden-set score distributions — never reuse a threshold
   across models. Rerankers likewise emit raw logits (Identity activation
   for MiniLM; unbounded CE scores for bge-reranker) — the harness must fix
   a normalization (sigmoid) and calibrate on golden data.

4. **CPU latency at pipeline scale.** Stage-1 embeds every fresh job and
   stage-2 reranks a shortlist (`SHORTLIST_SIZE`=250 → `TOP_N`=10). Measure
   wall-clock on the target CPU for: 250 postings embedded per encoder, and
   250 (query, posting) pairs through each reranker. The 278M
   bge-reranker-base vs 22.7M MiniLM gap (~12x params) is the decisive
   unknown — if bge-reranker-base blows the nightly budget on CPU, quality
   is moot. If *both* 768-dim encoders miss the latency/storage budget,
   bge-small-en-v1.5 (384-dim, QUALIFIED) is the documented fallback to pull
   back into the eval.

5. **NLI label-order safety and entailment thresholding.** The two NLI
   candidates order labels oppositely (MoritzLaurer: 0=entailment;
   cross-encoder: 0=contradiction). The harness must read `id2label` from
   config at runtime and include a canary test (a trivially entailing pair)
   that fails loudly if the mapping is wrong. It must also measure the
   entailment-vs-neutral boundary on golden claim-supports-requirement
   pairs — including adversarial near-misses (right skill, wrong depth) to
   test whether MoritzLaurer's ANLI training buys real robustness over the
   cross-encoder model's higher clean-set accuracy.

6. **512-token truncation of job postings.** Eight of nine models truncate
   at 512 tokens, silently. The eval must count how many golden-set postings
   exceed 512 tokens after templating and test a chunking strategy
   (chunk-then-max-score vs head-only) for both encoders and rerankers.
   Only if truncation measurably hurts should nomic's 8192-token context be
   revisited — that is the sole fact-sheet differentiator that would justify
   re-opening the bi-encoder shortlist.

7. **Benchmark trust.** Since 100% of the scores in this matrix are
   self-reported, the golden-set numbers are the *first* independent
   measurements in this pipeline. Eval results override every table above by
   design.
