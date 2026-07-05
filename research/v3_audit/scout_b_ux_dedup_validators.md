# Scout B — UX, Dedupe, Validators, Provenance, Ranking, Missing-Problem Handling

Audit slice of `docs/ARCHITECTURE_V3.md` (sections 7, 8, 9, 10, 13, 14 of the 22-section audit).
Evidence: live `jobpilot.db` (read-only), `app/domain/par_validation.py`, `app/services/claim_extraction.py`, `app/domain/roster.py`, `app/services/roster.py`, `app/domain/claims.py`, `app/domain/chunking.py`, `app/services/claim_review.py`, `web/components/claims.tsx`, `docs/V2_AUDIT.md`, and `docs/REVIEW_LAYER_AUDIT.md` (read via `git show 2375ba9:docs/REVIEW_LAYER_AUDIT.md` — the file exists only on branch `docs/review-layer-audit`, NOT on the audited branch, even though ARCHITECTURE_V3.md line 3 cites it as its predecessor).

## Critical flaws

- **§3.7's cross-entity duplicate detection is unimplementable as written.** Synthesis is "One DEEP-tier call per confirmed entity" (§3) whose input is only that entity's evidence and claims — a per-entity call structurally cannot "detect that two *confirmed entities* are the same project" (§3.7). No cross-entity pass exists anywhere in the doc.
- **Duplicate Results across entities exist live and nothing dedupes them.** Claims 472 (OneWorld) and 513 (cameron-morreale-portfolio) cite the identical outcome quote "Top 3 out of 100+ teams". The doc's "final Master CV must not contain duplicate bullets" guarantee is enforced nowhere — §3.7 dedupes only *within* a story.
- **"Cross-project is unrepresentable" (§3.2) is false for container entities.** `cameron-morreale-portfolio` is one confirmed entity whose evidence spans several distinct projects; blending inside it is fully representable, and the Top-3 duplication proves it happens.
- **§6 retires the per-claim 409 flag gate wholesale while §4 approves cited claims "over existing machinery" — a contradiction, and a regression** from REVIEW_LAYER_AUDIT §3, which kept integrity flags (coupling, ungrounded tools, duplicate spans — 78 live flags) blocking approve-as-is. V3 gives those flags no destination at all.
- **"Compose and elevate" Problems are checked for ref *existence*, not entailment.** S1 checks orphan refs and unsupported numbers; nothing checks that `supporting_refs` actually support the composed text. In V2, problem/action evidence links carry zero quotes (live DB: 150 action + 39 problem links, all quote-less), and v3 adds none — a plausible-but-unsupported Problem renders on the card labeled "[evidenced]".
- **The interaction estimate is ~2× low and the corpus shape is wrong.** 15 confirmed entities, not 12 (DS4635, Jobpilot, Paper recommender system have zero claims — Jobpilot has 116 assigned chunks and 7 recorded extraction *failures*; Paper recommender system has zero evidence). Realistic surface: ~15 cards + ~9–12 typed answers ≈ 24–30 interactions, not "~15".
- **A story that fails S1 structural validation has no state.** §7 S1 says it "fails synthesis validation and records why", but §2's status enum has no failed/quarantine value and §4's card list has no place for it — fatal failures are log-only, invisible to the user, exactly like today's `result.dropped`.
- **Ranking regressed from a deterministic spec to LLM self-scoring.** REVIEW_LAYER_AUDIT specified a deterministic leverage score (4/3/2/1 + tie-breakers, `domain/review_ranking.py`); v3 replaces it with `career_signal_score`/`result_strength_score` emitted by the same LLM being ranked, with no scale, no calibration, no tests, and an undefined "best existing claims" heuristic fallback (S2).

---

## 7. User Review UX Audit

**Verdict: the reframe is right and survives; the doc's numbers, the drill-down, and the absence of a specified debug boundary do not.**

### Review-item count on the live corpus

The doc claims "~12 cards, ~7 questions → ~15 interactions" (§4). Recount against the live DB:

- **Cards.** §2: `project_stories` has "one row per confirmed entity (unique)". Live DB: **15 confirmed entities** (`select count(*) from experiences where status='confirmed'` → 15), not 12. Twelve have pending claims; three do not: `DS4635` (10 assigned chunks, 0 claims), `Jobpilot` (116 assigned chunks, 0 claims, **7 `extraction_failure` rows in `validation_runs`**), `Paper recommender system` (0 assigned evidence, 0 claims). The doc never says whether an evidence-less or claim-less entity gets a story row; per §2 as written, all 15 do. **15 cards.** (Also 15, not ~12, DEEP synthesis calls — §3 and §8's "~12 calls / no new LLM spend beyond ~12" undercount, and Jobpilot's group is the expensive one: 116 chunks including an 11KB README.) [auditor: "11KB README" is wrong — Jobpilot's largest *assigned* chunk is a 2,696-char commit; all chunked README rows are ≤1,200 chars, and the >20KB README rows in `evidence` are legacy whole-document rows, unassigned. The 116-chunk count, the 15-entity roster, and Jobpilot's 7 `extraction_failure` rows are all verified against the live DB (2026-07-05): 15 confirmed entities, 12 with pending claims; the parent brief's "12 entities" counts only claim-bearing entities — for review-surface estimates all 15 confirmed entities count, since §2 gives every confirmed entity a story row.]
- **Questions.** Per-entity live claim stats (claims / problem-bearing / result-bearing):
  - Plausibly resume_ready without questions (≥1 problem + ≥1 result claim): MassDEP 20/3/7, Wellington 16/5/5, OneWorld 16/7/1, portfolio 12/4/7, Embue 11/3/6, Oasis 11/2/2, BoardGameGeek 11/7/5 → **7 cards, 0 questions** (optimistically — the problem texts still have to clear §3.1's bar).
  - needs_problem: investment-decision-workflow-engine 8/0/1, Stock Market Simulation 5/0/2 → **2 questions**.
  - needs_result: Cooper.ai 3/3/0 → **1 question**.
  - needs **both**: personal-knowledge-os 28/2/0 — its only two problem texts are "pgvector migration is blocked because PG18 has no compatible binary" and "pgvector has no pre-built PG18 binary available", one-line technical bugs that fail §3.1's own "higher-level than a one-line bug" requirement — and AI-Recovery-navigation 7/1/0, whose sole problem is "The .gitignore Python lib/ rule was incorrectly excluding…" (same failure). → **4 questions**.
  - The 3 claim-less entities: each needs a classify decision, and P+R answers if kept → **3 classifications + 0–6 answers**.
- **Merge prompts.** 1 is *warranted* by the data (OneWorld ↔ portfolio share the Top-3 result — see §8) but **0 are detectable** by the mechanism the doc specifies (see Critical flaws).

**Total: ~15 cards + ~7–13 typed answers + ~3 classifications ≈ 24–30 interactions.** Roughly double the doc's estimate — but still minutes, not hours. **A real user would tolerate this.** The reframe (one decision per project) is the correct fix for the 148-card queue; only the arithmetic is wrong.

### What should NEVER appear in normal review

Raw claim atoms as queue items; validator flag strings (`action_tool_not_in_text: declared tool 'SQL' does not appear…`); fragment actions; the `resolves` coupling machinery; near-duplicate candidates as separate items; drop/dedupe/extraction-failure logs; chunk span refs (`#chars=1204-1688`). The doc gets the queue right: story cards only.

### Advanced/debug mode

**The doc specifies no debug mode at all.** Claim inventory, `validation_flags`, `validation_runs`, slop metrics, and extraction hashes need a home the doc never provides.

### The drill-down re-leak

§4: "**Edit** → per-component drill-down (**existing edit-attest flow**)"; §7 S4: "claim drill-down retained inside the card". The *existing* flow is `web/components/claims.tsx`, which renders raw validation flags in full (`claims.tsx:170-181`), the raw P/A/R claim text, and the "flagged — attest the impact or reject; approve-as-is is disabled" banner (`claims.tsx:222-225`). As written, clicking Edit on personal-knowledge-os re-exposes **28 raw claims, 26+ of them flag-walled** — claim-level review reintroduced one click deep, inside every card. It is no longer the queue (an improvement), but the doc neither redesigns the drill-down nor says which claims (pending? inventory? flagged?) it shows, nor hides the flags. **Yes, §4's drill-down as specified re-leaks extraction artifacts.**

## 8. Deduplication and Merge Audit

Checklist against the doc and the code:

| Scenario | Handled? | Where / why not |
|---|---|---|
| Duplicate resumes describing the same work | **Partly** | Roster: human merges file-shaped proposals (`merge_experiences`, `claims.py:815-819`); cross-experience claim dedupe is **exact-normalized-text only** (`claim_content_fingerprint`, `claims.py:921-929`; applied at `claim_extraction.py:494-519`). Reworded near-duplicates pass. |
| GitHub repo + resume mention of same project | **No** | Live counterexample: the tokenized-emissions hackathon work sits under both `OneWorld` (claim 472) and `cameron-morreale-portfolio` (claim 513), sharing the verbatim outcome quote "Top 3 out of 100+ teams". Roster merge can't fix it — the portfolio is a legitimate distinct container entity, not the same project. |
| Case study + invoice note on same project | **Same gap** — depends entirely on the `HeuristicChunkAssigner` token-overlap assigning both to one entity (`roster.py:137-167`); if they land on different entities, no downstream layer reconciles. |
| Multiple project aliases | **Yes** (roster) | `Experience.aliases` + `matches_name` (`claims.py:247-255`); live aliases are rich (Wellington has 5). Solved upstream of v3; v3 adds nothing and needs nothing here. |
| Same org/date/technology clusters | **No** | No clustering anywhere; roster dedupe is name/alias string match (`propose_experience` contract, `claims.py:789-796`). |
| Near-duplicate claims under one project | **Aspirational** | §3.7: synthesis "deduplicates overlapping actions and results across sources, preserves provenance, keeps the strongest version" — an instruction to the LLM, not a mechanism. No deterministic check, no metric in §5 (which counts orphans and invented metrics, not duplicates). Acceptable-ish: §8's required behavior says different claims under one project are fine, and grouping (§3.2) subsumes most of it. |
| Duplicate Results / same metric in multiple bullets | **No — the critical gap** | Within one extraction group, one outcome span supports one claim (`seen_outcome_spans`, `claim_extraction.py:308-337`; `DUPLICATE_OUTCOME_FLAG` ×7 live). That check is **per-group**: the cross-entity Top-3 duplicate sailed through. §3.7 dedupes within a story only. Two resume_ready stories can each carry the same metric → the same bullet twice on the final CV. |

**Is §3.7's cross-entity duplicate detection specified or aspirational?** Aspirational, and worse — self-contradictory: the detection is assigned to a synthesis stage whose input (§3) contains exactly one entity. There is no cross-entity comparison step, no fingerprint over story components, no shared-quote index. The one emitting path (`duplicate_needs_merge` "naming the counterpart") cannot know the counterpart's name.

**Is the "no duplicate sections or bullets in the final Master CV" guarantee enforced anywhere in the doc?** **No.** §3.4's gate is per-story; render enforcement (`build_snapshot_content` refusal) is per-entity completeness; §5's run-report metrics contain no duplicate-bullet or duplicate-metric count. Sections won't duplicate (unique `experience_id` per story — that part holds), but bullets and metrics can and, on the live corpus, would.

## 9. Validator Strategy Audit

**Baseline (code, verified):** `STRUCTURAL_CODES = {problem_not_specific, action_fragment}` (`par_validation.py:48`), plus cross-boundary citations (`_outside_group_codes`, `claim_extraction.py:229-250`) — all dropped pre-persistence (`claim_extraction.py:310-318`), logged to `validation_runs` as `dropped:{experience}`. Everything else is advisory flags, and `approve_claim` raises `FlaggedClaimApprovalError` on any flag (`claim_review.py:47-58`), surfaced as a 409.

**Fatal vs. repairable in v3, item by item:**

| Fatal failure | Quarantined before review? |
|---|---|
| Cross-project Result | Claim level: yes (dropped, code). Story level: S1 "cross-entity refs" fails synthesis validation — **but a failed story has no status and no user-visible destination** (§2's enum lacks one). Container-entity blending (§8) is invisible to both. |
| Invented metric | S1 "unsupported numbers → story fails synthesis validation" + §5 `invented_metric_count` must be 0. Specified — destination of the failed story again unspecified. |
| One-word Problem | Claim level: dropped (code). Story level: §3.1 lists prose requirements ("complete, meaningful… not a filename, job header, tagline") but S1's structural check list (orphan refs, cross-entity refs, unsupported numbers) **does not include them** — no artifact-pattern or specificity check on the *composed* problem is specified. |
| Fragment Action | Claim level: dropped (code). Story level: `action_summary` is synthesized prose with **no fragment/length check specified**. |
| Unsupported Result | Claim level: advisory flags only (`result_evidence_missing`, `outcome_quote_not_verbatim` — these queue, not drop). Story level: §3.3 requires evidence-backed or attested; the quantified path is gated by the number check; **the qualitative path's `outcome_quote` verbatim machinery is never mentioned at story level.** |
| Document-sized evidence as citation | **Nowhere.** V2_AUDIT §9 proposed a ≤1,200-char citation-eligibility validator; `par_validation.py` never got it, and v3 is silent. Live: claim 374 cites a 1,417-char commit as its Result evidence. |
| Job header/filename as Problem | Claim level: `_ARTIFACT_PATTERNS` structural drop (`par_validation.py:55-63,121-128`). Story level: contract prose only, no check in S1. |

**Repairable → targeted questions?** Missing Problem/Result → yes, exactly right (§3.1, §3.3, §3.6 — the strongest part of the doc). Weak wording → Edit (human-initiated, fine). Needs attestation → yes (existing machinery). Duplicate candidate → `duplicate_needs_merge` exists but its detection is broken (§8). Unclear alias → **not addressed** (no question type; roster's problem, but nothing routes it).

**Are validator flags hidden from normal users?** Only the problem-absence family is dealt with (§6: "problem-absence is story-level status, not claim noise" — correct, and it kills 111 of 189 live flags). The remaining **78 flags** (`action_tool_not_in_text` ×37, `result_problem_coupling` ×21, `action_names_no_tools` ×13, `duplicate_outcome_span` ×7) get no disposition: not shown on cards (good), not translated into questions, and visible verbatim in the "existing edit-attest flow" drill-down (`claims.tsx:170-181`). The user stops debugging validator output at the queue level and resumes doing it one click deep.

**Does v3 address/retire the `FlaggedClaimApprovalError` 409 gate coherently?** No. §6 retires it ("per-claim approve 409 gate … retire"), but §4 says story approval runs "all over existing machinery" and "the claims its components cite are approved" — on the live corpus **130/148 claims are flagged**, so nearly every cited claim would throw `FlaggedClaimApprovalError` through that machinery. Either the gate stays and story approval breaks, or it's deleted and a claim carrying `action_tool_not_in_text` (an ungrounded tool name that flows into skills/bullets) becomes approved with no per-claim attestation and no story-level tool-grounding recheck (none is specified). REVIEW_LAYER_AUDIT §3/R2 had the right split — reclassify the problem-absence family, **keep integrity flags blocking** — and v3 dropped that distinction. This is a truthfulness regression, not a simplification.

## 10. Evidence and Provenance Audit

**Per-evidence-item requirements vs. reality:**

| Required | v3 / current state |
|---|---|
| source id | Yes — `evidence.id`, referenced by component `evidence_refs` (§3.2/§3.3) and `component_refs` (§2). |
| source type | Yes — `evidence.source_type` (verified schema). |
| normalized text | Yes — `chunk_text` over `normalize_source_text` (`roster.py:101,113,125`). |
| span offsets | **Hack, not schema**: encoded as `source_ref` suffix `#chars=start-end` (`claims.py:871-890`); 633/761 live rows carry one. Commits and legacy whole-doc rows have none. Quote-level offsets (`quote_start/quote_end`, proposed in V2_AUDIT §7) were never built and v3 doesn't add them. |
| project id | Yes — `evidence.experience_id` (roster assignment). |
| quote | **Results only.** `claim_evidence.outcome_quote` exists solely for `field='result'` (37 quoted live). Problem and Action links carry **no quote at all** (live: 39 problem + 150 action links, zero quotes) — a P/A citation means "somewhere in this ≤1200-char chunk". v3 adds no quote requirement for the composed Problem's `supporting_refs`. |
| field supported | P/A/R only (`ClaimField`, `claims.py:87-92`). **No Technology field** — §3.2 has per-action `tools` but no evidence link type supporting them; V2's `action_tool_not_in_text` flag was the only tool-grounding check and v3 retires flags without a replacement. |
| confidence | Named per action/result in §3.2/§3.3 JSON — no scale, no producer spec (the LLM self-reports), no downstream use (low confidence does not trigger a question). Decorative as specified. |
| human attestation | Yes — existing `user_attestation` machinery (`claims.py:998-1005`, `SOURCE_USER_ATTESTATION`), correctly reused by §3.1/§4. |

**Traceability questions:**

- *Every final bullet → selected P/A/R components?* Specified: `bullets_json` … "each with `component_refs`" (§2) and §5's `orphan_component_count` must be 0. But it lives in JSON blobs with no FK integrity — enforcement is one eval metric, not the schema.
- *Every component → evidence or attestation?* Same answer: specified via `orphan_component_count`, blob-level only.
- *Every number in generated prose → evidence or attestation?* **Results: yes** (§3.3 "V2 verbatim/number-factuality gates", §5 `invented_metric_count`). **Problem Space and `technical_details` prose: no** — the number gate is stated only in the Results section; a composed Problem saying "used by ~160 professionals" is checked by nothing.
- *Citation precision?* Drive/README chunks are genuinely ≤1,200 chars now (`MAX_CHUNK_CHARS = 1200`, `chunking.py:27`, paragraph-aligned with exact spans — verified). Commits are atomic but **unbounded**: 10 assigned commit chunks exceed 1,200 chars live (max 2,696) [auditor: max corrected from "1,514" — re-queried: the assigned >1,200 commit lengths run 1,242–2,696; the count of 10 is verified], and claim 374 cites a 1,417-char commit for both Action and Result. Legacy whole-document rows (up to 22.7KB) still sit in the `evidence` table — unassigned, so they can't feed roster-mode synthesis, but nothing prevents citing them and no validator checks citation size (see §9).
- *Can evidence from one project leak into another?* At the ref level, no: synthesis input is assembled per entity from `list_assigned_evidence` (`claim_extraction.py:352-381`), and S1 checks cross-entity refs. Three real leak vectors remain: (1) **container entities** — §3.2's "cross-project is unrepresentable" is disproven by `cameron-morreale-portfolio` carrying another entity's result; (2) **chunk misassignment** — `HeuristicChunkAssigner` is token-overlap with tie-refusal (`roster.py:144-167`), but a chunk mentioning two projects goes wholly to the higher-overlap one; (3) **composed text** — the LLM can write content its refs don't contain (refs are existence-checked, not entailment-checked), which is a leak from the model's prior rather than another project's evidence.

## 13. Ranking and Selection Audit

**Does v3 have a real ranking model?** No. §3.8 is a list of ten criteria ("strength of Problem Space, technical depth, business/user relevance, … seniority signal") with **no algorithm, no weights, no computation owner, no output scale, and no tests**. The only concrete score fields are `career_signal_score` (§3.2) and `result_strength_score` (§3.3), and by §3's contract ("One DEEP-tier call … Output: exactly the structure below") they are **emitted by the LLM inside the synthesis JSON** — the model ranks its own output, unvalidated, non-deterministic. §7 S2's offline heuristic "assembles a draft story from the entity's best existing claims" with "best" undefined.

**Did v3 lose specificity relative to REVIEW_LAYER_AUDIT?** Yes, unambiguously. That doc specified: "**Leverage ranking** (deterministic, no LLM): verified quantified Result (4) > qualitative/attested Result (3) > problem-bearing Action (2) > bare Action (1); tie-breakers: flag-free first, more evidence links, shorter cleaner text", housed in `domain/review_ranking.py` (R1), with "top ones pre-selected" as the default. v3 supersedes it (line 3: "its remedy is upgraded") and replaces a testable deterministic function with two named LLM output fields. Nothing in v3 forbids reusing the leverage score, but nothing requires it either — and the file specifying it isn't even on this branch.

**Coverage of the required ranking targets:** clearest Problems / strongest Results / highest-leverage Actions — nominally, via §3.8's criteria list and §3.2's "prefer high-leverage system-building". Best bullets — §3.5 gives content rules (no generic "improved efficiency", mechanism + outcome) but no ranking among the 1–3 candidates. Resume-ready projects — ranked ordering of the queue "strongest stories first" (§3.8). **Exclusion candidates — not ranked at all**: `exclude_low_value` exists as a status (§2) but no criterion, threshold, or recommendation for *which* stories to propose excluding is specified; the burden of spotting low-value projects stays with the user.

**Does ranking reduce review burden? Are defaults recommended? Can the user override?** In intent, yes to all three: synthesis output *is* the default (pre-selected components, ranked actions per the §4 card mock), the queue orders strongest-first, and §4's Edit allows "component swaps". But because the scores have no spec, "reduces review burden" rests entirely on unvalidated LLM judgment — there is no way to test S6's ≤15-interaction exit criterion against a ranking that has no definition.

## 14. Missing Business Problem Handling

**What v3 gets right (and it is the doc's best section):** never fill a missing Problem — `needs_problem` + one targeted question (§3.1); typed answers become `user_attestation` evidence with `problem_status=user_attested` (existing, real machinery — `plan_claim_edit`/`SOURCE_USER_ATTESTATION`, `claims.py:998-1052`); the resume_ready gate (§3.4) requires an evidenced-or-attested Problem **and** Result before anything renders, enforced at synthesis, review (disabled approve), and render (`build_snapshot_content` refusal) — so a purely technical demo cannot become a resume section without the user supplying both. [auditor: cross-file note — this restates the doc's *promised* enforcement; in current code the render refusal does not exist (`build_snapshot_content` filters only approved-claim + confirmed-entity, and an approved action-only claim renders — proven by `tests/test_claims_routes.py:249-279`). Scouts A/C's finding that §3.4 states future behavior in the present tense is confirmed.] The worked example (§0) correctly shows unproven metrics marked `user_attestation_needed`, never filled. This matches the required behavior for the "never invent" and "stay portfolio_inventory without P AND R" rules.

**Where the "compose and elevate" relaxation breaks (the attack):** the README→business-bullet path is: README chunk (≤1200 chars, assigned to the project entity) → claims that are overwhelmingly problem-less (111/148 `problem_missing` live — commit and README evidence describes work, not why it mattered) → §3.1 synthesis "may *compose and elevate* what the evidence supports". The only stated guards are (a) "every composed problem carries `supporting_refs`" and (b) "a problem the evidence cannot support is not written". Guard (b) is **an instruction to the same LLM doing the composing** — self-policing. Guard (a) is checked by S1 as an *orphan-ref* check: the refs must exist and point at real chunks of this entity. **Nobody checks that the refs entail the composed text.** There is no verbatim requirement (explicitly relaxed), no entailment check (deterministic or LLM), no quote on problem refs (V2 problem links already carry none — verified live), and the §5 number gate covers metrics only, not Problem prose. A README's aspirational framing ("democratizes recovery support…") can be elevated into a confident business problem statement, ref'd to the chunk it loosely paraphrases, and presented on the review card labeled **[evidenced]** (§4 card mock) — the V1 "polished but unsupported" failure reborn at the Problem layer, now with a UI label that actively overstates its support. The Result gate still holds (numbers can't be invented), so the fabrication risk is *framing*, not metrics — but a resume bullet's business-impact framing is precisely what §3.1 exists to police.

**Tentative inference:** the required behavior ("infer tentative Problems only when context supports") has no middle state in v3 — a problem is either written (and shown as evidenced) or `needs_problem`. There is no `tentative`/`inferred` problem_status prompting confirmation; the `confidence` field (§3.2/3.3) exists for actions/results but not for the Problem Space at all.

**Technical implementation vs. career story:** the statuses exist (`evidence_only`, `portfolio_inventory`, §2) but the **assignment rule doesn't** — nothing distinguishes "no problem found, ask the user" (`needs_problem`) from "purely technical demo, don't bother asking" (`portfolio_inventory`). Status assignment happens "at synthesis" (§3.4), i.e., the LLM decides which of seven statuses applies with no criteria. Live corpus consequence: personal-knowledge-os and AI-Recovery-navigation both have technically-flavored one-line "problems" (pgvector binary compatibility; a `.gitignore` bug) that fail §3.1's bar — whether they become questions or portfolio inventory is unspecified, and the answer changes the review surface and the CV.

## Unknowns

- Whether a cross-entity dedupe/merge-detection pass exists in the authors' intent but was omitted from the doc — as written, §3.7's mechanism contradicts §3's per-entity input.
- Whether the doc's "~12" deliberately excludes the 3 claim-less confirmed entities (it never says; §2's unique-per-confirmed-entity rule implies 15 story rows).
- Whether evidence-less confirmed entities (`Paper recommender system`, 0 chunks) get a story row, a card, or are skipped — unspecified.
- What the story drill-down actually shows (pending claims only? inventory? flags?) — S4 says "claim drill-down retained inside the card" with no content spec.
- Whether the S2 heuristic synthesizer would adopt REVIEW_LAYER_AUDIT's deterministic leverage score ("best existing claims" is undefined).
- Why `validation_runs` contains no `extraction_eval` rows (kinds present: `par_validation` ×522, `extraction_failure` ×10, `interview_verification` ×2) — the Phase-4 scorecard path may never have run against this DB; not further investigated (read-only slice).
- Why Jobpilot's extraction failed 7 times (the failure detail rows weren't inspected beyond counts) — relevant because the doc's worked example is the one entity the current pipeline cannot extract.
- `docs/REVIEW_LAYER_AUDIT.md` exists only on branch `docs/review-layer-audit` (commit 2375ba9); whether it is intended to merge before v3 lands is unknown — right now v3's "Supersedes" pointer dangles on the audited branch.
