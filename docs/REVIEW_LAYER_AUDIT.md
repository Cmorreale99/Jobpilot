# Jobpilot Review-Layer Audit — the queue nobody will click through

**Date:** 2026-07-05 · **Branch:** `main` (audit phases 1–4 merged) · **Evidence base:** live `jobpilot.db` (148 pending claims across 12 confirmed entities)

**Scope:** the human review experience — queue shape, flag semantics, decision unit, and the resume-worthiness bar.

> **Root cause.** The extraction boundary was fixed (claims now live under real entities), but the **review layer still operates at extraction granularity**. The unit a human actually decides is the *project* — "does this earn a resume spot, and which bullets represent it best?" — yet the queue asks 148 claim-sized micro-questions, flags the expected sparsity of commit evidence as if it were a defect, and enforces P-A-R completeness at the one level where absence is honest (the claim) while never enforcing it at the level where absence is disqualifying (the project).

---

## 1. The evidence

Live queue at audit time — one row per confirmed entity (`claims / with-problem / with-result`):

```text
 28 /  2 /  0   personal-knowledge-os
 20 /  3 /  7   MassDEP — Technical Business Analyst (Data & AI Systems)
 16 /  5 /  5   Wellington Management — Data Architecture & Engineering Intern
 16 /  7 /  1   OneWorld
 12 /  4 /  7   cameron-morreale-portfolio
 11 /  3 /  6   Embue — Data Systems Architecture Lead
 11 /  2 /  2   Oasis
 11 /  7 /  5   BoardGameGeek async data pipeline
  8 /  0 /  1   investment-decision-workflow-engine      ← zero problems
  7 /  1 /  0   AI-Recovery-navigation                   ← zero results
  5 /  0 /  2   Stock Market Simulation                  ← zero problems
  3 /  3 /  0   Cooper.ai — Data Engineer (Contract)     ← zero results

148 pending · 130 flagged (88%)
flag breakdown: problem_missing ×111 · action_tool_not_in_text ×37 ·
                result_problem_coupling ×21 · action_names_no_tools ×13 ·
                duplicate_outcome_span ×7
```

The five reported problems, confirmed:

1. **148 manual decisions.** At ~15 seconds a card that is 35+ minutes of clicking; realistically it never gets done. The queue's size is set by extraction granularity (one card per commit-derived claim), not by decision granularity (one per project).
2. **Some projects state no business problem.** `investment-decision-workflow-engine` (0/8) and `Stock Market Simulation` (0/5) have *zero* problem-bearing claims; `AI-Recovery-navigation` (1/7), `personal-knowledge-os` (2/28), and `Oasis` (2/11) are near zero. Commit messages describe work, not why the work mattered — this is a property of the evidence, not an extraction bug.
3. **88% of the queue is flagged**, and 111 of the flags are `problem_missing` — i.e. the validator marks the *expected state of commit evidence* as a per-claim defect. A flag that fires on 75% of claims carries no information; the review card reads as a wall of warnings and trains the user to ignore flags entirely (including the 21 coupling flags that actually matter).
4. **The same project appears up to 28 times.** `personal-knowledge-os` is 28 separate cards. The user's real question about it — "is this resume-worthy, and what's its best bullet?" — is answerable in one look and one gesture, not 28.
5. **No project-level P-A-R bar.** Four entities cannot currently render a complete Problem-Action-Result story from extracted evidence alone (two lack problems, two lack results — and `personal-knowledge-os` and `AI-Recovery-navigation` lack results too). Nothing in the system says so; the user would discover it only after approving claims and rendering a hollow section.

## 2. Failure taxonomy

| # | Failure | Evidence | Cause | Fix |
|---|---|---|---|---|
| 1 | Review effort scales with claims, not projects | 148 cards / 12 entities | Queue unit = claim (extraction granularity) | One review card per entity; claim list becomes card content, not queue items |
| 2 | Missing problems discovered too late (or never) | 2 entities at 0 problems, 3 near-zero | P-A-R completeness never evaluated per project | Project P-A-R bar: no render without ≥1 Problem AND ≥1 Result among the project's approved claims; the card asks for the missing piece as ONE attested statement or excludes the project |
| 3 | Flag noise drowns flag signal | 111/189 flags are `problem_missing` | Evidence sparsity encoded as claim defect | Reclassify the problem-absence family: not a `validation_flag`, but project-level completeness metadata. Claim flags reserved for integrity defects (coupling, ungrounded quotes, tool mismatch) |
| 4 | No selection concept | 28 candidates for one project, all demanding individual verdicts | Only terminal dispositions exist (approve/reject) | New non-terminal disposition `shelved` = "not chosen" (≠ rejected = "false"); a project decision approves the selected few and shelves the rest without polluting the golden set |
| 5 | No leverage ordering | Candidates listed in extraction order | No ranking | Deterministic leverage score orders candidates; top ones pre-selected |

The golden-set distinction in #4 matters: rejections are retained as "this extraction was wrong" labels. Bulk-rejecting 25 unchosen-but-true claims would poison that corpus. `shelved` keeps them re-selectable (tailoring against a specific job may later want a different bullet than the resume default) and keeps the labels honest.

## 3. The redesign — project-centric review

One screen, ~12 cards, each card one decision:

```text
┌─ personal-knowledge-os · project · 28 candidates ──────────────────────────┐
│ P-A-R status: ✗ no Problem stated  ✗ no Result evidenced                   │
│                                                                            │
│ Selected bullets (ranked by leverage, top 3 pre-checked):                  │
│  [x] Built the ingestion layer for … (action-only)                         │
│  [x] Implemented semantic search over … (action-only)                      │
│  [ ] 25 more candidates ▸                                                  │
│                                                                            │
│ To include this project, complete its story (one statement each):         │
│  Problem: [ what hurt before this existed?                       ]        │
│  Result:  [ what changed after it shipped?                       ]        │
│                                                                            │
│  [ Approve project ]           [ Not resume-worthy — exclude ]             │
└────────────────────────────────────────────────────────────────────────────┘
```

Semantics of **Approve project** (one API call, orchestrating existing machinery):

1. The typed Problem/Result statements become `user_attestation` evidence attached to the lead selected claim via the existing edit-attest plan (provenance preserved; `result_status=user_attested`).
2. Selected claims → `approved`.
3. Unselected claims → `shelved` (new status; reversible; excluded from queue and CV).
4. The project passes the P-A-R bar and becomes renderable.

Semantics of **Exclude**: entity → `discarded` (existing roster machinery); its pending claims are shelved. One click retires `Stock Market Simulation` and its 5 cards.

**The P-A-R bar (the user's rule, enforced twice):**
- *Review-time:* the Approve button is disabled until the selection + typed statements collectively contain ≥1 Problem and ≥1 Result (actions are guaranteed — every claim has one).
- *Render-time (defense in depth):* `build_snapshot_content` omits any entity whose approved claims don't collectively carry ≥1 Problem and ≥1 Result — a project without a complete story can never appear on the CV, no matter how it got approved.

**Leverage ranking** (deterministic, no LLM): verified quantified Result (4) > qualitative/attested Result (3) > problem-bearing Action (2) > bare Action (1); tie-breakers: flag-free first, more evidence links, shorter cleaner text. Per-claim review (edit a specific bullet, reject a false one) remains as drill-down inside the card.

**Flag reclassification:** `problem_missing`/`problem_not_pain_point` stop being `validation_flags` (they currently also block approve-as-is via the 409 gate — actively wrong for action-only claims a project card wants to select). They become card-level completeness state. Integrity flags (coupling, ungrounded quotes, tool mismatches, duplicate outcome spans) stay per-claim and keep blocking approve-as-is. Expected flag rate after reclassification: ~25% — flags become signal again.

**Estimated review effort after redesign:** 12 cards; 7 need one or two typed statements; total ≈ 15–20 interactions, ~5 minutes. Same decisions, two orders of magnitude less clicking.

## 4. What this does NOT change

- The claim state machine's terminal semantics (approved/rejected retained forever) — `shelved` is additive and non-terminal.
- Extraction, the roster, chunking, grounding, the number gate — all untouched.
- The snapshot/render contract — it gains one filter (the P-A-R bar), nothing else.
- The golden set — it gets *cleaner* (rejections stay meaningful).

## 5. Implementation plan

### R1 — domain (pure)
`shelved` claim status + transitions (`pending_review → shelved`, `shelved → pending_review`); leverage score (`domain/review_ranking.py`); project completeness (`project_par_status(claims) -> missing: {problem?, result?}`); the render-time P-A-R bar in `build_snapshot_content`.

### R2 — flag reclassification
The problem-absence family no longer persists as `validation_flags` (and stops triggering the approve-as-is 409); surfaced instead through project completeness. Integrity flags unchanged.

### R3 — service + API
`GET /review/projects` (cards: entity, ranked candidates, completeness, pre-selection); `POST /review/projects/{id}/decide` (`selected_claim_ids`, optional `problem_statement`/`result_statement`, or `exclude=true`) — orchestrates attest → approve → shelve atomically over existing repo operations.

### R4 — dashboard
The claims-review section becomes the project-cards section (per-claim drill-down retained inside cards). The Missing-Results queue disappears — its job is absorbed by card completeness.

### R5 — exit tests
- 148-claim live-shaped fixture reviews to completion in ≤ (entities + typed statements) interactions.
- A project approved without a Result cannot exist (button gate) and cannot render (snapshot bar).
- Unselected claims are shelved, never rejected; golden set contains only true verdicts.
- An excluded entity never renders and its claims leave the queue.

## 6. Verdict

Phase 2 moved the *extraction* boundary from files to projects; this audit moves the *decision* boundary the same way. The 88% flag rate and the 148-card queue are the same class of error the original audit named — enforcing the right rules against the wrong unit of scope. The fix is one new status, one ranking function, one completeness bar, one endpoint, and one card component; everything else is already built.
