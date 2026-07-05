# Scout D — Evaluation Harness, Red-Team, Simplicity, Implementation Plan (audit §17–20 of docs/ARCHITECTURE_V3.md)

**Evidence base:** docs/ARCHITECTURE_V3.md (all sections), docs/V2_AUDIT.md §10–13, docs/REVIEW_LAYER_AUDIT.md (NOT on this branch — read from branch `docs/review-layer-audit`, commit 2375ba9), code as cited, live data (148 pending claims / 12 confirmed entities, `problem_missing` ×111, per-entity P/R coverage as listed in §18). [auditor: entity count corrected — the live DB has **15** confirmed entities; 12 is the count *with pending claims* (claim-less: DS4635, Jobpilot, Paper recommender system). 148 pending, all flag counts, and the per-entity P/R distribution verified exact against `jobpilot.db`, 2026-07-05.]

## Critical flaws

- **The single `status` enum cannot represent compound gaps.** ARCHITECTURE_V3 §2 lists one status per story (`needs_problem | needs_result | needs_action | …`), but the live corpus's worst entity — personal-knowledge-os, 28 claims / 2 problems / 0 results — needs *both* a Problem and a Result. There is no representable state for that. Readiness must be a derived struct (which fields are missing), not a status.
- **§3.7's `duplicate_needs_merge` is unimplementable as specified.** §3 defines synthesis input as "the entity…, its assigned evidence chunks, and its extracted claim inventory" and §3.2 celebrates that "cross-project is unrepresentable" — so a per-entity synthesis call structurally *cannot* "detect that two confirmed entities are the same project." Duplicate detection has to be a separate cross-entity pass (deterministic evidence-overlap works); the doc puts it in the wrong component.
- **Story-approve vs. the claim-level flagged-409 gate is unspecified.** §4 says "Approve story → the claims its components cite are approved," but `approve_claim` refuses any claim with `validation_flags` (`app/services/claim_review.py:47-58`, `FlaggedClaimApprovalError`). 130/148 live claims are flagged. Either story-approve fails on most stories, or it bypasses the gate and auto-approves integrity-flagged claims (ungrounded quotes, coupling violations). REVIEW_LAYER_AUDIT §3/R2's integrity-vs-absence flag reclassification solved exactly this; ARCHITECTURE_V3 §6 just says the 409 gate "retire[s]" and drops the distinction.
- **The legacy per-file fallback creates CONFIRMED entities with zero human review.** `run_claim_extraction` falls back to per-file grouping when no confirmed roster exists (`app/services/claim_extraction.py:427-437`) and then calls `repository.upsert_experience` (line 456), which hard-codes `ExperienceStatus.CONFIRMED` (`app/db/claim_repository.py:127-128`, `app/services/claim_repository.py:51-52`). Under v3, "one story per confirmed entity" would synthesize and queue story cards for file-shaped entities nobody confirmed. ARCHITECTURE_V3 §1 claims the roster stage is "V2, unchanged" and never mentions this hole.
- **"Keeps the strongest version" (§3.7) is an inflation amplifier.** When near-duplicate sources conflict — the existing fixture pair literally encodes this: `tests/fixtures/roster/drive/resume_v1.md` ("cutting export failures in half") vs `resume_v2.md` ("…in half across all regions") — preferring the "strongest" version systematically selects the most inflated wording. Nothing in v2 or the v3 doc detects staleness, inflation, or cross-source metric conflict.
- **The synthesized-Problem gate is human-only, and the human can't see the evidence.** `supporting_refs` (§3.1) can be machine-checked for existence and entity ownership, but not for entailment — whether the composed prose is actually supported is a semantic judgment. That's an acceptable trade *only if* the review card shows the referenced quotes next to the composed Problem; the §4 card mock shows an `[evidenced]` tag and no quotes. Separately, §4's "Answer a question → typed text becomes attested evidence" applies no structural validation — a one-word answer becomes a `user_attested` Problem and passes the gate, while v2 drops one-word problems structurally (`problem_not_specific`, `app/domain/par_validation.py:48`).
- **`confidence`, `career_signal_score`, `result_strength_score` (§3.2–3.3) are specified nowhere** — no scale, no formula, no consumer beyond §3.8's ten-adjective ranking wishlist. The heuristic default synthesizer cannot compute them meaningfully; the LLM will emit vibes-numbers. And a *deterministic* synthesizer that authors Problem prose is a template slop generator — the heuristic must be restricted to component selection + status assignment, never prose.
- **S6's exit tests cover roughly 4 of the ~16 tests this layer needs.** Missing entirely: one-project-once (review and CV), fatal quarantine, repairable→targeted-question, duplicate-merge-prompt, no-duplicate-sections, bullet→component tracing, and legacy-path-closure tests. Detail in §17.

---

## 17. Evaluation Harness

### Existing assets to reuse

- **Fixtures:** `tests/fixtures/roster/` — one multi-project word-per-line-mangled case-study doc (`drive/case_studies.md`: Wellington platform rebuild + PFAS reporting), two near-duplicate resumes with *conflicting result wording* (`drive/resume_v1.md` / `resume_v2.md`), one repo with README + commit (`github/carrier-etl.md`). `tests/fixtures/real_world/drive/resume_export_mangled.md` — two-job mangled resume with contact header and word-number metrics ("twelve hours to two hours"). `tests/fixtures/claims/` — the full-loop fixture set used by `tests/test_full_v2_loop.py`.
- **Patterns:** `_Scripted` extractor stub (`tests/test_roster_service.py:242`) for injecting hand-built outputs; the end-to-end API-driven loop with docx-level assertions (`tests/test_full_v2_loop.py:36-151`); the snapshot belt-and-suspenders tests (`tests/test_master_cv_snapshot.py:82,118,228`).
- **Harness:** `app/domain/evaluation.py` (`compute_slop_metrics`, golden set) + `app/tools/eval_extraction.py` + `validation_runs` kind `extraction_eval` (`app/services/claim_extraction.py:544-554`). §5's `eval_stories` extends this.
- **Gap:** no live-shaped fixture exists. `tests/fixtures/roster` yields ~4 entities and a handful of claims. The single most valuable new asset is a **live-shaped corpus fixture** (12 confirmed entities, ~148 claims matching the real P/R distribution: 28/2/0, 20/3/7, 16/5/5, 16/7/1, 12/4/7, 11/3/6, 11/2/2, 11/7/5, 8/0/1, 7/1/0, 5/0/2, 3/3/0) [auditor: distribution verified exact, but the live roster is **15** confirmed entities — the fixture must also carry the 3 claim-less confirmed entities (Jobpilot 116 chunks/0 claims, DS4635 10/0, Paper recommender system 0 evidence), since §2 gives every confirmed entity a story row], built as a pytest factory or seeded fixture (`app/tools/seed_v2_demo.py`, currently untracked, is a starting point). S6 *names* this corpus but never says to build the fixture. **Build it first** — V2's core evaluation failure (V2_AUDIT §13: "every test runs on pre-labeled fixtures that no real document resembles, so the suite is green while production is garbage") repeats otherwise.

### Test specifications

Code locations follow the doc's S1–S6 plan: `app/domain/project_story.py` (S1, new), `app/services/story_synthesis.py` (S2/S3, new), `app/api/stories.py` (S4, new), `app/domain/master_cv_snapshot.py` (S5, modified), `app/tools/eval_stories.py` (S6, new), migration `app/db/migrations/versions/0012_create_project_stories.py`.

**T1 — 148 raw claims collapse into ~12 Project Capsules.**
- Fixture: the live-shaped corpus factory (12 confirmed entities, 148 pending claims).
- Expected: `run_story_synthesis` produces exactly 12 `project_stories` rows; `GET /stories` returns 12 cards; per-claim cards no longer exist in the review surface. [auditor: on the real corpus, §2 as written yields **15** rows (one per confirmed entity, 3 of them claim-less), not 12 — pin the expected count to the fixture's confirmed-entity count and force an explicit decision on claim-less/evidence-less entities, which the doc leaves unspecified.]
- Failure: >12 rows, or any entity with 2+ stories, or the claim queue still served as the review unit.
- Location: `tests/test_story_synthesis.py` (new), against `app/services/story_synthesis.py`; DB uniqueness in migration 0012 (unique `experience_id`).

**T2 — same project from resume + GitHub + notes appears once.**
- Fixture: `tests/fixtures/roster/` — confirm the carrier-etl proposal, merge the two Coopersmith resume proposals via the existing roster merge (`merge_experiences`, `app/domain/claims.py:815`), assign, extract, synthesize.
- Expected: one story per surviving confirmed entity; the merged (`ExperienceStatus.MERGED`) entity has no story.
- Failure: a story row exists for a merged or discarded entity.
- Location: `tests/test_story_synthesis.py`; guard in `run_story_synthesis` (iterate `CONFIRMED` only — mirror `gather_roster_groups`, `app/services/claim_extraction.py:352-381`).

**T3 — one project appears once in review.**
- Fixture: same as T2.
- Expected: `GET /stories` returns one card per confirmed entity, no duplicates by `experience_id`.
- Failure: duplicate cards.
- Location: `tests/test_story_routes.py` (new), against `app/api/stories.py`.

**T4 — one project appears once in the Master CV.**
- Fixture: two resume_ready stories approved; snapshot built.
- Expected: `build_snapshot_content` emits each `experience_id` at most once across both sections.
- Failure: same entity in both sections or twice in one.
- Location: extend `tests/test_master_cv_snapshot.py`; enforcement in `app/domain/master_cv_snapshot.py::build_snapshot_content` (S5 rewrite).

**T5 — project without Problem is not resume_ready.**
- Fixture: entity with the investment-decision-workflow-engine shape (8 claims, 0 problems, 1 result).
- Expected: synthesized story has readiness missing=problem (doc: `needs_problem`); `POST /stories/{id}/approve` → 409; story never renders.
- Failure: resume_ready without an evidenced/attested Problem, or approve succeeds.
- Location: gate in `app/domain/project_story.py` (pure, §3.4); tests in `tests/test_project_story.py` + `tests/test_story_routes.py`.

**T6 — project without Result is not resume_ready.**
- Fixture: AI-Recovery shape (7 claims, 1 problem, 0 results) — also Cooper.ai shape (3/3/0).
- Expected/Failure/Location: as T5 with missing=result. This is S6's promised "a story without a credible Result cannot render" — plus the approve-gate half S6 omits.

**T7 — technical GitHub project without business context → needs_problem or portfolio_inventory.**
- Fixture: `tests/fixtures/roster/github/carrier-etl.md` + its commit (action-only evidence), or the personal-knowledge-os shape (28/2/0).
- Expected: heuristic synthesizer assigns needs_problem and authors **no Problem prose**; LLM synthesizer (fake-client scripted, `app/llm/fake.py` pattern) with an unsupported composed problem fails the ref check.
- Failure: any synthesizer emits `problem_status=evidenced` with zero `supporting_refs`, or the heuristic emits Problem prose at all.
- Location: `app/domain/project_story.py` structural check (`problem_status=evidenced` ⇒ ≥1 valid ref — this IS deterministically checkable; entailment is not, see §18-10); `tests/test_story_synthesis.py`.

**T8 — fatal validator failures do not enter normal review.**
- Fixture: `_Scripted`-style synthesizer (pattern: `tests/test_roster_service.py:242`) emitting a story with (a) an orphan component ref, (b) a ref to another entity's evidence, (c) a bullet number in no cited chunk.
- Expected: story fails synthesis validation, is recorded in `validation_runs` (kind `story_eval` or a `story_validation` kind), and is **not** queued `pending_review` — mirroring extraction's drop path (`app/services/claim_extraction.py:310-319, 483-492`).
- Failure: a structurally invalid story reaches `GET /stories`.
- Location: S1 structural checks in `app/domain/project_story.py`; drop path in `app/services/story_synthesis.py`.

**T9 — repairable missing fields become targeted questions.**
- Fixture: T5/T6 entities.
- Expected: `questions_json` contains exactly the §3.6 question(s) for the missing field(s) — one for needs_problem, one for needs_result, both for a both-missing entity (see Critical flaw #1) — and none for present fields.
- Failure: zero questions on a non-ready story, or a question wall.
- Location: pure derivation function in `app/domain/project_story.py` (`derive_questions(readiness) -> tuple[Question, ...]` — deterministic, no LLM needed; §3.6 is a fixed taxonomy keyed on missing fields); `tests/test_project_story.py`.

**T10 — duplicate project candidates become merge prompts.**
- Fixture: extend `tests/fixtures/roster/drive/` with two resume variants whose *titles differ* ("Coopersmith Data — Data Engineer" vs "Coopersmith — DE role") so roster name/alias dedupe does NOT collapse them; confirm both; assign; synthesize.
- Expected: a cross-entity pass flags the pair (evidence/claim-fingerprint overlap above threshold) → `duplicate_needs_merge` naming the counterpart → resolved via existing roster merge.
- Failure: two independent resume_ready stories for the same real project.
- Location: **cannot live in the per-entity synthesizer** (Critical flaw #2) — a deterministic service-level pass in `app/services/story_synthesis.py` comparing assigned-evidence overlap between confirmed entities. This test forces the design fix.

**T11 — final Master CV contains no duplicate sections.**
- Fixture: full loop on the roster fixtures (pattern: `tests/test_full_v2_loop.py:149-151` docx XML assertions).
- Expected: each entity name appears exactly once in `word/document.xml`; context JSON has unique `(section, experience_id)` pairs.
- Failure: repeated headings (the original V2 symptom).
- Location: `tests/test_full_v3_loop.py` (new, cloned from the v2 loop test).

**T12 — every rendered project has approved P, A, and R.**
- Fixture: mix of resume_ready and non-ready stories, one non-ready story force-transitioned to approved via a buggy-caller simulation.
- Expected: `build_snapshot_content` refuses/omits any entity lacking an evidenced-or-attested Problem, ≥1 Action, and an evidenced-or-attested Result — the render-time third enforcement §3.4 promises (belt-and-suspenders like the existing approved-only filter, `app/domain/master_cv_snapshot.py:80`).
- Failure: a P- or R-less entity in `content_json`.
- Location: extend `tests/test_master_cv_snapshot.py` (pattern of `test_builder_drops_unapproved_claims_even_if_a_caller_passes_them`, line 118).

**T13 — every rendered bullet traces to selected story components.**
- Fixture: resume_ready story with 3 bullets carrying `component_refs`.
- Expected: every bullet's refs resolve to Action/Result components present in that story; every component's `evidence_refs` resolve to claim/evidence rows of the same entity; the docx-level walk (pattern: `tests/test_full_v2_loop.py:123-146`) maps each rendered bullet back to its story.
- Failure: orphan bullet or orphan component (`orphan_component_count > 0` — §5 promises the component half; the bullet→component half it does not).
- Location: S1 structural check + `tests/test_full_v3_loop.py` + `eval_stories` metric.

**T14 — every number in output appears in evidence or attestation.**
- Fixture: `tests/fixtures/real_world/drive/resume_export_mangled.md` (word-numbers: "twelve analyst hours") and a digit-metric fixture.
- Expected: every numeric token (digits AND number-words — the v2 verbatim gate is text-substring, `verbatim_in`, `app/domain/par_validation.py:82`, so word-numbers pass if verbatim) in rendered bullets/results appears verbatim in a cited chunk or in a `user_attestation` evidence row. `invented_metric_count == 0` (§5 promises the metric; the test must include the *bullet prose*, not just `results_json` — §5 is ambiguous on which text is scanned).
- Failure: any free-floating number.
- Location: `app/domain/project_story.py` number check reusing `verbatim_in`; `app/tools/eval_stories.py`; `tests/test_project_story.py`.

**T15 — V1/legacy paths cannot feed user-facing prose.**
- Already covered in code: V1 builder deleted (commits 4adcf54, 62ba643); pipeline is V2-only (`app/services/pipeline.py:8-11`); legacy master_cv rows invisible (`tests/test_master_cv_snapshot.py:228,236`); tailoring highlights carry claim ids with hallucinated ids dropped (`app/llm/drafting.py:11`, `app/domain/tailoring.py:70-77`).
- NOT covered: the per-file extraction fallback (Critical flaw #4). New test: with no confirmed roster, `run_claim_extraction` must not create `CONFIRMED` experiences (or `run_story_synthesis` must refuse entities that never passed roster review). **This inverts `tests/test_roster_service.py:334` (`test_no_confirmed_roster_falls_back_to_per_file_groups`), which currently asserts the fallback works.**
- Location: `app/services/claim_extraction.py` (Phase 0 change) + `tests/test_claim_extraction_service.py`.

**T16 — attested answers are structurally validated (not in the required list, but mandatory).**
- Fixture: needs_problem story; `POST /stories/{id}/answer` with "yes" / one word.
- Expected: the answer is refused or the gate stays closed — reuse the structural problem validator (`problem_not_specific`) on attested Problem text.
- Failure: one-word attested Problem flips the story resume_ready. (See §18-9.)
- Location: `app/api/stories.py` answer endpoint + `app/domain/project_story.py`.

### What the doc's §7/S6 already promises vs. missed

**Promised (S6 + §5):** live-shaped corpus reviews in ≤15 interactions (≈T1, but "interaction" is undefined — the exit criterion is unmeasurable as written); story without a credible Result cannot render (T6/T12 render half); no invented metric survives (T14, prose-scope ambiguous); Jobpilot worked example's unproven metrics marked never filled; `orphan_component_count == 0` (T13 component half); S1 names structural checks that imply T8's quarantine (but no test is stated for the *not queued* behavior).

**Missed entirely:** T2/T3/T4 (one-project-once at every layer), T5 approve-gate half, T7, T9 (targeted-questions correctness), T10 (merge prompts — unimplementable as spec'd anyway), T11 (duplicate sections), T13 bullet half, T15 (legacy closure — the doc's §1 "V2, unchanged" actively obscures the fallback hole), T16. Also missing from §5's metric list: `duplicate_story_count` (must be 0) and any cross-source-conflict/staleness signal.

---

## 18. Red-Team Scenarios

**(1) Four resumes describe the same project differently.**
Should: one entity (roster merge), one story; differently-worded duplicates of the same action/result collapse to one component each, provenance preserved. V3: entity level handled by V2 roster (§3.7 "already solved by the roster"; code: `merge_experiences`, `app/domain/claims.py:815-817`; proposals dedupe by name+alias, `app/db/claim_repository.py` `_upsert`). Within-story: §3.7 says synthesis "deduplicates overlapping actions and results across sources" — prompt-level only; the claim-level content-fingerprint dedupe (`app/services/claim_extraction.py:494-520`) is exact-text and *different wording defeats it*. No structural near-duplicate check exists or is planned. Component: StorySynthesizer + a missing S1 near-dup check. Test: 4-variant fixture → `actions_json` contains no near-duplicate pair (normalized token-Jaccard threshold); T2.

**(2) GitHub README describes implementation, no business Problem.**
Should: needs_problem or portfolio_inventory, targeted question, no invented context. V3: handled explicitly and well — §3.1 (`needs_problem` + the exact question) and §3.6. Live shape: personal-knowledge-os 28/2/0. Component: synthesizer status assignment + the `supporting_refs` structural check. Test: T7. Caveat: the heuristic default must not author prose here (§19).

**(3) Case-study PDF contains three unrelated projects.**
Should: chunks route to three entities; anything unroutable is quarantined visibly. V3: chunk assignment is "V2, unchanged" (§1) — `run_roster_assignment` + `HeuristicChunkAssigner` refuse ties (`app/domain/roster.py:163-164`) and unassigned chunks "never feed extraction" (`app/services/roster.py:203`). **Silence:** if the third project was never proposed/confirmed, its chunks stay unassigned forever with no surface — `RosterAssignmentReport.unassigned` is a log line, not a queue. Nothing in ARCHITECTURE_V3 mentions unassigned evidence. Component: roster service + a missing run-report line in §5's output. Test: 3-project doc, 2-entity roster → third project's chunks counted unassigned and surfaced in `eval_stories`; nothing from them appears in any story. (Fixture: extend `tests/fixtures/roster/drive/case_studies.md`, currently 2 projects — V2_AUDIT §12 test 5 asserts ≥3 with a fixture name, `case_studies_wellington_llm_infra.pdf`, that does not exist in the repo.)

**(4) Strong Actions, no Result.**
Should: needs_result, one question, render blocked; action-only content still selectable. V3: handled — §3.3, §3.4, §3.6. Live shapes: AI-Recovery 7/1/0, Cooper.ai 3/3/0. Component: resume_ready gate (S1) + snapshot bar (S5). Test: T6, T9, T12.

**(5) A Result but no clear Problem.**
Should: needs_problem; and once a Problem is attested, the Result must actually *address* it. V3: needs_problem handled (§3.1). **Silence:** v2's coupling rule (`result_metric_json.resolves` must match a declared pain point — `app/domain/par_validation.py:271`; 21 live `result_problem_coupling` flags) is never restated at story level. A story could pair an attested Problem with an evidenced Result that resolves something else. Component: S1 needs a story-level coupling check (or at minimum the card must display P and R adjacently so the human judges — it does). Test: story with attested Problem "X" and Result resolving "Y" → flagged or question raised.

**(6) A metric appears in one source but belongs to another project.**
Should: unrepresentable. V3: strong where assignment is correct — extraction rejects outside-group citations (`_outside_group_codes`, `app/services/claim_extraction.py:229-250`; `tests/test_roster_service.py:250`), synthesis input is per-entity (§3.2), `compute_slop_metrics.boundary_clean` hard-fails on cross-links (`app/domain/evaluation.py:69-71`). **The weak link is upstream: a misassigned chunk makes everything downstream "structurally clean" but wrong.** The token-overlap `HeuristicChunkAssigner` is conservative (ties → None) but not correct; there is no human review surface for chunk assignments, and the doc is silent on assignment error. Component: ChunkAssigner + the story card's evidence quotes (the human catch — quotes must be visible, see (10)). Test: metric chunk with tokens overlapping two entities → assigned None, not guessed (exists in spirit at `tests/test_roster_service.py:173`); story-level: a story citing a chunk assigned to another entity fails S1 validation (T8b).

**(7) Two projects share technologies and organization.**
Should: remain distinct; ambiguity resolves to unassigned, not misassigned; no false merge prompt. V3: roster human confirmation is the real defense (V2); assigner refuses ties. The §3.7 merge signal can't fire falsely because it can't fire at all (Critical flaw #2); once reimplemented as evidence-overlap, a threshold too low will false-positive on shared-tech entities. Component: the T10 cross-entity pass — threshold must be tested against a shared-tech-distinct-projects fixture. Test: two Embue-like entities sharing tool tokens → no `duplicate_needs_merge`; their chunks assign or honestly None.

**(8) A stale resume contains outdated, inflated wording. (Hardest case — v3 has nothing.)**
Should: the freshest/most conservative supported version wins; conflicts between sources become a *question*, not a silent pick. Reality: v2's verbatim gate makes inflated source text *pass* — the resume IS the evidence, so "garbage-in, verbatim-out" is working as designed; provenance (`modified_time` exists on Drive sources, `pushed_at` on repos) is captured but never consulted. V3 makes it *worse*: §3.7 "keeps the strongest version," which under conflicting duplicates is a systematic pick-the-biggest-number rule (Critical flaw #5). The existing fixture pair (resume_v1 "cut export failures in half" vs resume_v2 "in half across all regions") is precisely this scenario and no test exercises the conflict. Nothing in v2 or the doc detects staleness or inflation — the only mitigations available without an LLM judge are (a) recency preference using stored `modified_time`, (b) a deterministic conflict detector: two sources supporting the same component with different metric values/scopes → surface as a targeted question ("your sources disagree: 'X' (May) vs 'Y' (June) — which is current?"). Component: to-be-created conflict check in S1 + question taxonomy extension in §3.6 (which today has no conflict question). Test: resume_v1+v2 both assigned to one entity → story carries a conflict question; neither wording auto-selected as "strongest."

**(9) A user attests a Result not present in evidence. (Trust-by-design — acceptable? Auditable?)**
Should: allowed (it's the user's own career and the entire point of `user_attestation`), but permanently distinguishable from evidence and never laundered into "evidenced." Reality: this is v2's best-built machinery and it IS auditable — attested text persists as an evidence row with stable ref `claim:<id>:<field>` (`_attestation_chunk`, `app/domain/claims.py:998-1005`), `result_status=user_attested` survives into snapshot content (`app/domain/master_cv_snapshot.py:57`), and the golden set exports the `attested` flag (`app/domain/evaluation.py:213`). Verdict: acceptable for a single-user tool; the user lying to themselves is out of scope, and the audit trail means a future employer-facing distinction is recoverable. Two real gaps: (a) **no structural validation on attested text** (T16 — v2 validates *extracted* problems harder than *typed* ones, which is backwards); (b) no re-check when later-ingested evidence contradicts an attestation (acceptable to defer; log it as a known limit). V3 doc: §3.1/§4 restate the machinery correctly; silent on both gaps. Test: T16; plus attestation survives re-synthesis (`replace_unreviewed` semantics for stories — §2 "replaced idempotently like claims" must mean *never* replacing answered/approved stories; needs an explicit test).

**(10) The LLM proposes an attractive but unsupported Problem Space. (The deliberate relaxation.)**
Should: composed-but-supported prose allowed; unsupported prose impossible to mark as evidenced. V3: §3.1 requires `supporting_refs` and says unsupported problems "are not written." But "supported" is not machine-checkable — the deterministic checks can only verify refs exist, belong to this entity, and are non-empty (do that: `problem_status=evidenced` ⇒ ≥1 valid same-entity ref, S1). Entailment between refs and prose is exactly the judgment the verbatim gate used to make mechanical; v3 moves it to the human. That's a defensible trade **only if the card renders the referenced chunk quotes under the composed Problem** — the §4 mock shows `[evidenced]` with no quotes, so the reviewer would be approving a label, not checking a claim (Critical flaw #6). An LLM emitting plausible refs that don't actually support the prose passes every automated gate. Component: S4 card contract (must include `chunk_text` for problem refs — the v2 queue already does this for claims, `tests/test_full_v2_loop.py:74-80`); optionally a cheap lexical-overlap sanity flag (composed problem shares almost no content words with its refs → flag for review), which is deterministic and offline. Test: story card API returns quotes for every problem ref; `evidenced` with zero refs is unrepresentable (T7); lexical-overlap flag fires on a scripted mismatch.

**(11) A validator flags most candidates.**
Should: flags that encode evidence sparsity stop being flags; integrity flags keep blocking. Live reality: 130/148 flagged (88%), 111 = `problem_missing` — which extraction deliberately queues as advisory (`_UNFIXABLE_BY_REEXTRACTION`, `app/services/claim_extraction.py:226`) and review then 409-blocks (`app/services/claim_review.py:52`). V3: §6 says problem-absence becomes "story-level status, not claim noise" — right idea, but the doc never says what happens to the *existing flags on claims a story cites at approve time* (Critical flaw #3). REVIEW_LAYER_AUDIT R2 specified the fix (reclassify the absence family; integrity flags keep blocking); ARCHITECTURE_V3 dropped it. Component: story-approve semantics in S3/S4. Test: story citing absence-flagged claims approves cleanly; story citing an `outcome_quote_not_verbatim`-flagged claim in a Result component cannot approve without edit-attest.

**(12) The corpus has 300 source artifacts.**
Should: cost and context stay bounded; per-entity evidence is budgeted. V3: synthesis is per-entity not per-source (good — call count tracks roster size, not corpus size), `synthesis_hash` skips unchanged entities (§2), prompt-cached evidence block (§3). **Silences:** roster detection proposes over ALL documents in one pass (`app/llm/roster.py` LLM proposer — 300 docs in one prompt?); a single entity accumulating hundreds of commits produces an unbounded evidence block per synthesis call; chunk assignment cost grows linearly but the LLM assigner's prompt size doesn't obviously bound. No evidence-budget/ranking-for-inclusion is specified anywhere. Component: S3 input builder needs a deterministic per-entity evidence budget (rank chunks, cap, record exclusions in the run report). Test: 300-chunk entity → synthesis input ≤ budget, exclusions logged, deterministic selection (same input → same subset).

---

## 19. Simplicity / Overengineering Audit

### The minimum viable v3

The problem being solved: **148 micro-decisions nobody will make, on an inventory that can't say which projects are resume-worthy.** The MVP that solves it:

1. **Phase 0 closure** (delete the per-file fallback's CONFIRMED upsert; split integrity/absence flags) — prevents new slop.
2. **A pure readiness computation** over the existing claims ledger: per confirmed entity, does its claim set contain ≥1 evidenced Problem and ≥1 evidenced Result? Which are missing? (This is REVIEW_LAYER_AUDIT's `project_par_status` — ~50 lines, no LLM, no migration.)
3. **A derived question list** from the fixed §3.6 taxonomy (pure function of readiness).
4. **A project-card review endpoint** that selects claims, attests typed P/R answers via existing `plan_claim_edit` machinery, and approves — one decision per entity.
5. **The render-time P-A-R bar** in `build_snapshot_content`.

That is 12 cards, ≤15 interactions, no invented prose, zero LLM spend, and no new table (approval state lives on claims; readiness is computed). The LLM synthesis layer — composed Problem paragraphs, ranked strategic actions, candidate bullets — is a *quality upgrade on top of* this, not the foundation. Notably, REVIEW_LAYER_AUDIT §5–6 already described almost exactly this MVP ("one new status, one ranking function, one completeness bar, one endpoint, one card component; everything else is already built"); ARCHITECTURE_V3 supersedes it with a heavier design and its §6 claim that "`shelved` is no longer needed — uncited claims simply stay inventory" is the one genuine simplification it adds (correct: no status churn needed if the story cites rather than transitions).

### Correctly deferred (keep deferring)

Vector DBs / embedding RAG (V2_AUDIT §13: ledger IS the retrieval layer at this scale), LLM judges, RL/self-improvement (CLAUDE.md out-of-scope), autonomous full-Drive ingestion (`GDRIVE_ALLOW_BROAD_SCAN=false` stands), multi-user SaaS assumptions (everything is `user_id`-scoped already; build nothing more), advanced UI polish, elaborate scoring models.

### V3's own overengineering

- **Ten statuses, not eight, and most aren't states.** §2 lists `draft, pending_review, resume_ready, needs_problem, needs_result, needs_action, evidence_only, portfolio_inventory, duplicate_needs_merge, exclude_low_value`. Only four are lifecycle states (draft, pending_review, approved/resume_ready, excluded). `needs_problem/needs_result/needs_action` are **derived facts** — encoding them as statuses can't represent compound gaps (Critical flaw #1) and creates transition-matrix combinatorics. `needs_action` is near-unreachable (every extracted claim has an Action by schema; an entity with zero claims is `evidence_only`). `evidence_only` vs `portfolio_inventory` is a distinction without a behavioral difference (neither renders; collapse them). `exclude_low_value` duplicates roster `DISCARDED` (`app/domain/claims.py:128` already allows `CONFIRMED → DISCARDED`). `duplicate_needs_merge` isn't a state of *this* story, it's a relation between two entities — a flag/prompt, not a status. **Verdict: 4 statuses + a readiness struct + a merge-prompt list.**
- **`confidence` + `career_signal_score` + `result_strength_score`:** spec'd nowhere (no range, no formula, no reader). §3.8's ranking is ten unweighted adjectives. Cut all three fields; if the review queue needs ordering, reuse REVIEW_LAYER_AUDIT's deterministic 4/3/2/1 leverage score — defined, testable, sufficient at n=12.
- **The heuristic StorySynthesizer default:** partially meaningful, partially dangerous. Meaningful: status/readiness assignment, component *selection* from existing claims, question derivation — all deterministic and correct. Garbage: any deterministic authoring of Problem paragraphs or bullets is template slop with an `evidenced` label — false confidence in exactly the layer v3 exists to make trustworthy, and with API credits exhausted it's what would actually run against live data first. **Rule: the heuristic selects and gates; only the LLM or the user authors prose.** A heuristic-synthesized story shows the selected claims' own text verbatim, marked as such. (Precedent: v2's `HeuristicRosterProposer` deliberately reproduces dumb behavior *as proposals* — the analogous honesty here is "no prose, just selection.")
- **`questions_json` as first-class objects:** over-modeled. §3.6's questions are a closed taxonomy keyed on missing fields — derivable at read time from readiness. Persisting them adds a sync problem (answer a question → must invalidate stored siblings). Store the *answers* (they're attestations — already a table); derive the questions. Acceptable exception: a persisted free-text question only if the LLM synthesizer genuinely asks something outside the taxonomy — YAGNI until observed.
- **`bullets_json` at synthesis time:** premature. The render unit can be the story's P/A/R directly (§3.5's "one concise Problem paragraph…" IS the rendered form). Candidate-bullet generation multiplies the surface the number-gate and tracing tests must cover, before the story layer itself has proven out. Defer to after S5 renders plain stories.
- **`synthesis_hash`:** justified — mirrors the proven `extraction_hash` economics (migration `0011`, skip logic `app/services/claim_extraction.py:458-463`). Keep.

### Must be deleted (not deferred)

The per-file fallback's ability to mint CONFIRMED entities (`app/services/claim_extraction.py:427-437` + `upsert_experience`); the claims-queue-as-review-surface once story cards ship (§6 promises this — hold it to that); `tests/test_roster_service.py:334`'s assertion that the fallback works (invert it).

---

## 20. Implementation Plan

Constraint honored throughout: **Anthropic credits are exhausted.** Every phase below is buildable and testable offline (heuristics + fake LLM client `app/llm/fake.py` + fixtures); the only credit-dependent step is the final live synthesis run (~12 DEEP calls) and it is last.

### Phase 0 — stop user-facing slop (days)

**Already done (verified):** V1 structuring path deleted (commits `4adcf54`, `62ba643`); pipeline is V2-only — zero approved claims ⇒ no matching/drafting/prose (`app/services/pipeline.py:8-11,141-151`); legacy `master_cv` rows invisible to reads (`tests/test_master_cv_snapshot.py:228,236`); tailoring/outreach ground highlights by approved-claim id, hallucinated ids dropped (`app/llm/drafting.py:11`, `app/domain/tailoring.py:70-77`); snapshot double-filters approved+confirmed (`app/domain/master_cv_snapshot.py:80-93`).

**Not done — the audit prompt's suspicion is confirmed, and it's worse than "fallback exists":** the legacy per-file fallback in `run_claim_extraction` (`app/services/claim_extraction.py:427-437`) doesn't just group by file — `upsert_experience` at line 456 creates **CONFIRMED** experiences (`app/db/claim_repository.py:127-128`), i.e. file-shaped entities that v3 story synthesis would treat as human-confirmed.

- **Files:** `app/services/claim_extraction.py` (delete the fallback branch; no confirmed roster ⇒ loud no-op report, mirroring `run_roster_assignment`'s refusal at `app/services/roster.py:207-211`); `app/services/claim_review.py` + `app/domain/par_validation.py` (flag reclassification: absence family — `problem_missing`, `problem_not_pain_point` — stops blocking approve; integrity flags keep the 409). Optionally keep per-file grouping reachable only via an explicit dev flag that upserts `PROPOSED`.
- **Schema:** none.
- **Migration risk:** none. Data risk: existing file-shaped CONFIRMED experiences from past fallback runs in the live DB should be audited (one-off query), not migrated blindly.
- **Tests:** invert `tests/test_roster_service.py:334`; extend `tests/test_claim_review.py` for the flag split (`test_flagged_claim_cannot_be_approved_as_is` at line 96 splits into integrity-blocks / absence-allows).
- **What NOT to do:** don't build any story code; don't touch extraction prompts; don't delete the loud-failure logging pattern.
- **Outcome:** nothing user-facing can be fed by an unreviewed boundary; the 409 wall (130/148 unapprovable) is gone before the story layer arrives.

### Phase 1 — Project Capsule foundation (mostly already built — verify, don't rebuild)

**The roster already provides:** normalization at every gather path (`normalize_source_text`, `app/services/roster.py:101,113,125,141`; `app/domain/text_normalization.py`); LLM+heuristic entity proposal with human confirm/merge/rename/discard (`app/services/roster.py`, `app/domain/roster.py`, state machine `app/domain/claims.py:118-130`); aliasing + both-direction name/alias dedupe; span-ref chunk assignment with honest unassignment (`app/services/roster.py:190-257`); one-entity-once by construction; migration `0010`.

- **Gaps to add:** (a) surface unassigned-evidence counts beyond a log line (they belong in the §5 run report — red-team 3); (b) the deterministic cross-entity evidence-overlap check that replaces §3.7's impossible synthesis-time duplicate detection (T10) — a pure function in `app/domain/roster.py`, reported, resolved by existing merge.
- **Schema:** none. **Tests:** T10 fixture variant; unassigned-surface test. **Offline:** fully.
- **What NOT to do:** no new tables, no re-clustering machinery, no embedding-based assignment.

### Phase 2 — candidate extraction + ranking + fatal quarantine + repairable questions (the S1 core, pure and offline)

Extraction-side quarantine already exists at claim level (structural drops never queued — `app/services/claim_extraction.py:310-319`; validated by `tests/test_roster_service.py:250,271` and `tests/test_evaluation.py:158`). This phase builds the **story-level domain layer**:

- **Files:** new `app/domain/project_story.py` — readiness struct (NOT statuses; Critical flaw #1), resume_ready gate (§3.4), question derivation (§3.6 taxonomy, pure), deterministic leverage ranking (REVIEW_LAYER_AUDIT's 4/3/2/1), structural checks (orphan refs, cross-entity refs, number gate reusing `verbatim_in`), story fingerprint. New `tests/test_project_story.py`. Build the **live-shaped corpus fixture** here (T1's factory) — first, per the harness lesson.
- **Schema:** migration `0012` (`project_stories`) — defer to the END of this phase, only once persistence is actually needed (approval state, attested answers, synthesized prose). Unique on `(user_id, experience_id)`; FK to `experiences`; 4-value status + readiness snapshot columns; `synthesis_hash`.
- **Migration risks:** (a) roster merge after stories exist — merging entities must delete/invalidate the source entity's story (add to `merge_experiences` semantics + test); (b) "replaced idempotently like claims" must copy `replace_unreviewed_claims`' human-decision preservation exactly — an answered or approved story is never replaced by re-synthesis; (c) JSON component columns get no DB enforcement — every invariant must live in `project_story.py` (SQLite dev / Postgres prod both fine; `result_metric_json` precedent exists).
- **Tests:** T1, T5, T6, T8, T9, T13, T14 (domain halves). **Offline:** fully — no LLM anywhere in this phase.
- **What NOT to do:** no LLM synthesizer yet; no score fields; no bullets; no prose authoring in the heuristic (§19).

### Phase 3 — project-level review UX

- **Files:** new `app/services/story_synthesis.py` (`run_story_synthesis`: per confirmed entity, hash-skip, quarantine path, validation-logged — clone the extraction service's shape); new `app/api/stories.py` (`GET /stories`, `POST /stories/{id}/approve|answer|classify|edit`); `web/components/claims.tsx` → story cards (per-claim drill-down retained inside); answer endpoint reuses `plan_claim_edit`/`_attestation_chunk` machinery with **structural validation on attested text** (T16).
- **Approve semantics (the Critical-flaw-#3 fix, decided explicitly):** approving a story approves its *cited* claims through the normal state machine; absence-flagged claims approve cleanly (Phase 0 reclassified them); integrity-flagged claims block the story approve with a per-claim edit-attest prompt. The card API must include evidence `chunk_text` for every component ref *including the Problem's supporting refs* (red-team 10).
- **Schema:** none beyond 0012. **Tests:** T3, T15-new, T16, flag-interaction test (red-team 11); route tests cloned from `tests/test_claims_routes.py` patterns. **Offline:** fully (heuristic-selection stories or seeded rows).
- **What NOT to do:** don't remove the claims queue until the exit tests pass (§6 promises removal — sequence it after T1–T4 are green).

### Phase 4 — Master CV generation from structured approved stories

- **Files:** `app/domain/master_cv_snapshot.py` (`build_snapshot_content` takes resume_ready stories; render-time P-A-R bar — the third enforcement of §3.4); `app/domain/resume_context.py` (map story P/A/R into the **frozen** renderer's exact contract — `render_master_cv.py` and the template are never touched, CLAUDE.md rule); `app/services/master_cv_render.py` unchanged in shape.
- **Schema:** none. **Migration risk:** snapshot `content_json` shape changes — keep `snapshot_of` discriminator (`SNAPSHOT_KIND`) so old versions stay readable; version the new kind (`resume_ready_stories`).
- **Tests:** T4, T11, T12, T13/T14 render halves; `tests/test_full_v3_loop.py` cloned from `tests/test_full_v2_loop.py:36-151` including the docx XML negative assertion. **Offline:** fully.
- **What NOT to do:** no template edits; no bullet generation yet — render P/A/R prose directly first (§19).

### Phase 5 — tailoring/outreach from approved stories only

- **Files:** `app/domain/master_cv_snapshot.py::master_cv_from_snapshot` (adapt story snapshot → `MasterCv` bridge, provenance `story:<id>` mirroring `claim:<id>` at line 148); `app/domain/tailoring.py` + `app/llm/drafting.py` (highlight refs point at story components; hallucinated-id dropping unchanged in principle).
- **Tests:** extend the v2 pattern — zero resume_ready stories ⇒ zero experience content in outreach (pipeline already stops with no approved content, `app/services/pipeline.py:141-151`); every highlight traces to an approved story component. **Offline:** fully.
- **Last, credit-gated:** the LLM `StorySynthesizer` (S2's DEEP implementation) behind a `STORIES_LLM_SYNTHESIS` flag, tested against the fake client first; then the one live run (~12 DEEP calls) when credits return, scored by `eval_stories` (T14 metrics + `duplicate_story_count` + conflict count) before any card is reviewed.

### Reconciliation with the doc's S1–S6

Differences, and which ordering is safer:

1. **S1–S6 has no Phase 0.** The fallback closure and flag reclassification appear nowhere in the doc — yet without them, story-approve hits the 409 wall and synthesis consumes unreviewed file-shaped entities. Phase 0 first is strictly safer.
2. **The doc puts the LLM synthesizer + migration in S2, second.** With credits exhausted that stalls the plan at step two — and it front-loads the least verifiable component. Safer: domain gate → review → render on heuristic *selection* (no prose), LLM prose last. This also de-risks the §3.1 relaxation: the human-gated card and quote-display exist before any composed prose does.
3. **The doc defers all evaluation to S6.** That is V2's exact documented failure mode (V2_AUDIT §13: green suite, garbage production, because tests ran on unrealistic fixtures last). The live-shaped fixture and per-phase exit tests belong at the *start* of Phase 2.
4. **S4 replaces the claims queue in the same slice that introduces story cards.** Safer: additive first, delete after T1–T4 pass.
5. **S5's render change is where the frozen-renderer contract can silently break** — the doc doesn't mention the contract mapping at all; Phase 4 names it explicitly.
6. Agreements: S1's pure-domain-first instinct, `synthesis_hash` economics, S3's no-LLM re-gate on answers, and §8's no-data-loss migration stance (148 claims become inventory untouched; the one existing rejection stays golden-set data) are all correct and kept.

---

## Unknowns

- **docs/REVIEW_LAYER_AUDIT.md is not on this branch** (`v2/master-cv`) — it exists only on branch `docs/review-layer-audit` (commit 2375ba9), yet ARCHITECTURE_V3.md line 3 cites it by path. Whether this is an unmerged-branch oversight or intentional is unknown; either way the doc's "supersedes" reference dangles on the branch where v3 work would happen.
- Live-DB state was taken from the task brief + REVIEW_LAYER_AUDIT's numbers (148/12, flag breakdown, per-entity P/R). I did not query `jobpilot.db` directly; if the DB has changed since 2026-07-05 the shapes may differ (a `jobpilot.db.bak-2026-07-04` exists untracked, suggesting recent live activity).
- Whether past live extraction runs actually used the per-file fallback (i.e. whether file-shaped CONFIRMED experiences exist in the live DB now) — needs the one-off audit query proposed in Phase 0; I verified only that the code path creates them. [auditor: answered — per-file extraction did run live. The live DB holds six file-shaped experiences (ids 1–6: `cmorreale_resume.docx (1).pdf`, `Data Systems Case Studies (…) (2).pdf`, `Cmorreale_2026_finance_CV.pdf`, `Graduate Degree Completion Form_0 (1).pdf`, `Cmorreale Resume Sep 2025.docx`, `Cam_Morreale_resume.pdf`), all now `discarded` by a human; `validation_runs` also records an `extraction_failure` for the file-shaped group `Cmorreale_2026_finance_CV.pdf`. Ids 1–6 predate the roster entities (ids 7+); their original status (fallback-CONFIRMED vs roster-PROPOSED) is not recorded, but no file-shaped entity is currently confirmed, so Phase 0's data-risk audit query is effectively done.]
- `app/tools/seed_v2_demo.py` and `agent_kit.md` are untracked; their contents were not read, so whether seed_v2_demo already approximates the live-shaped fixture is unverified.
- The exact prompt-cache and context-size behavior of the planned DEEP synthesis call (red-team 12) is unverifiable pre-implementation; the ~12-call cost estimate in §3/§8 is taken at face value.
- V2_AUDIT §12 test 5 references a fixture `case_studies_wellington_llm_infra.pdf` that does not exist anywhere in the repo; whether that test was ever implemented under another name was not exhaustively traced (the roster fixture `case_studies.md` covers 2 projects, not ≥3).
