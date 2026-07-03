# Decision criteria — JobPilot V2 model stack

## Slots to fill

1. `bi_encoder` — embeds PAR claims and job-posting requirements for retrieval.
2. `reranker` — cross-encoder re-scoring of the retrieved shortlist.
3. `nli` — entailment check that a claim actually supports a requirement.

## Hard constraints (from candidates.yaml)

- CPU-friendly local inference (no GPU assumed).
- License permits commercial/personal automated use (MIT/Apache preferred).
- Bi-encoder dims <= 1024 (pgvector index practicality).
- English-only acceptable.
- Actively maintained or stable/widely adopted.

## Pipeline order

model-scout runs (parallel, one fact sheet per candidate)
-> benchmark-auditor (verifies/corrects sheets in place)
-> research-synthesizer (comparison matrix + shortlist of 2 per slot)
-> human review of research/model_comparison.md
-> golden-set eval harness (tests/eval/)
-> ADR (docs/adr/0001-model-stack.md)

## Governing rule

Research only narrows the field to exactly 2 qualified candidates per slot.
Winners are selected solely by the golden-set eval results — never by the
research phase, the synthesizer, or benchmark reputation.
