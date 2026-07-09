<!-- Recovered from the deleted /ultraplan cloud session (session 07efdee0…), extracted from the local remote-planning transcript cache. This is the plan you drafted and approved, verbatim. -->

# JobPilot v3.1 — Strict Problem Space → PAR Bundle → Selection → Bullet

## Context

JobPilot v3 is feature-complete but has correctness failures rooted in one structural gap: **there is no Problem Space or PAR Bundle layer.** Today `project_stories` is strictly one story per confirmed entity (`UniqueConstraint("experience_id")`, `models.py:309`), and `select_story_content` (`domain/project_story.py:528`) picks exactly **one** problem then pools **all** of the entity's actions and results into that single story. When an entity spans several distinct problem spaces (e.g. Cooper.ai / FedEx integrity vs. Pacifica automation vs. dataset delivery), they blend into one story — an action from one space can sit beside a result from another. There is also no user selection of a bundle / action / result; synthesis auto-selects.

Second, the heuristic result classifier `_is_outcome` (`domain/claims.py:516`) only accepts a result as **quantified** when it has *both* a metric *and* a canonical outcome verb (`reduced/cut/increased/...`), and as **qualitative** only via 4 hardcoded markers. This drops real data-engineering results:
- `"correcting overstated charge reporting from $4.01M to $2.16M"` → metric present but `"correcting"` isn't a canonical verb → **missed** (the defensible Cooper.ai result the system lost).
- `"restored 100% date coverage"`, `"removing 195K+ duplicate records"`, `"delivered five production Snowflake datasets"`, `"enabling daily warehouse refreshes"` → non-canonical verb and/or magnitude/number-word forms `_METRIC_RE` (`claims.py:357`) doesn't catch → missed or misclassified as work.

Third, the roster has no expected-project reconciliation: `Paper recommender system` exists as a claim-less entity in the corpus but there is no mechanism to report "expected but absent from parsed source" distinctly from a parsing failure.

**Intended outcome (v3.1):** detect multiple problem spaces inside one experience; present complete Problem→Action→Result bundles; let the user pick a bundle, then exactly one action and one result; block any bullet that mixes problem spaces; extract non-percentage results reliably; and reconcile expected-vs-detected projects. Existing v3 (multi-bullet story→docx render) stays intact — the single-selected-bullet flow is additive.

**Decisions locked with the user:**
1. Architecture: **generalize stories to problem spaces** — `project_stories` becomes one-per-(entity, problem_space) with candidate actions/results + `selected_action_id`/`selected_result_id`.
2. Scope of this increment: **backend engine + validators + tests first**; defer the `web/components` selection UI to a follow-up.
3. Generation: **new single-bullet path**, keep the existing story→docx multi-bullet render working (no regression to current output).

Follows repo conventions: `domain/` stays pure (no `llm/`/`integrations/real` imports), heuristic default with an LLM path behind a flag + `create_*` factory, schema owned by Alembic.

---

## Increments (smallest safe order)

### Increment 1 — Result-extraction fix (independent, highest value, lowest risk)
Fixes correctness #2/#3; unblocks the Cooper.ai regression test with no schema change.

- **`domain/claims.py`**
  - Add `ResultType` StrEnum: `quantitative | coverage | reliability | automation | operational | delivery | analytics | decision_enabling`.
  - Broaden `_METRIC_RE` (`:357`) to catch magnitude/count forms it misses: `195K+`, bare `$4.01M`/`$2.16M` without "from…to", `100%` standalone, and spelled counts (`five`, `three`) via a small number-word set (reuse the existing `_NUMBER_WORDS` in `project_story.py:648` — lift to a shared spot or mirror it).
  - Rework `_is_outcome` (`:516`) → return `(is_outcome, ResultKind, ResultType | None)`. Recognize the 8 result types by lexicon (`restored/recovered`→coverage, `prevented/stabilized`→reliability, `automated/eliminated … manual`→automation, `enabled … refresh/daily`→operational, `delivered/shipped … datasets/production`→delivery, `enabled … analytics/AI`→analytics, `enabled … validated/auditable/decision`→decision_enabling, plus `removed/corrected/reduced/…`→quantitative). **Decouple**: a result-typed statement is an outcome even without a canonical verb; a metric present → `QUANTIFIED`, otherwise `QUALITATIVE_EVIDENCED`.
  - Strengthen action-vs-result in `_classify` (`:755`): a statement matching a result-type lexicon classifies as `outcome`, never `work`, even under an ambiguous leading verb.
  - Thread `result_type` into `_ClaimAccumulator.to_draft` (`:656`) → store as `result_metric_json["result_type"]` (no DB column; `result_metric_json` is already JSON).
- **`app/llm/extraction.py`** — update `_PASS2_SYSTEM` (`:108`): enumerate the 8 result types, state that non-percentage outcomes (coverage/reliability/automation/operational/delivery/analytics/decision-enabling) are valid results, and "one result per candidate — never combine." Add optional `result_type` to the pass-2 JSON contract and carry it into `result_metric_json`. No grounding-logic change (verbatim gate unchanged).
- Guard against regressions in `par_validation.py::_validate_result` (`domain/par_validation.py`) — a `qualitative_evidenced` result still needs a verbatim `outcome_quote`; only the *classification* widened, not the grounding bar.

### Increment 2 — ProblemSpace / PARBundle domain + 4 validators (pure)
Fixes #1 and the contamination hard-rules; no persistence yet.

- **New `app/domain/problem_space.py`** (pure), dataclasses mirroring the desired data model, each field carrying `experience_id`, `project_id | None`, `problem_space_id`, `bundle_id` (where applicable), evidence refs (`claim_ids`), and `field_type`:
  - `BundleProblem`, `ActionCandidate`, `ResultCandidate` (adds `result_type`, `outcome_quote`), `PARBundle` (`bundle_id`, `problem`, `action_candidates`, `result_candidates`, `selected_action_id`, `selected_result_id`, `status`), `ProblemSpace` (`problem_space_id`, `label`, `scope`, `bundles`), and `BundleStatus` StrEnum (`requires_user_selection | ready | missing_result`).
  - `detect_problem_spaces(experience_id, claims, *, project_id=None) -> list[ProblemSpace]` — deterministic clustering of an entity's claims into problem spaces. Cluster key: shared pain-point tags (reuse `pain_point_tags`, `claims.py:562`) + problem/scope token overlap; stable `problem_space_id` via a content hash (mirror `component_id`, `project_story.py:121`). One bundle per distinct problem; `action_candidates`/`result_candidates` = that space's distinct actions/results (skip `ResultKind.MISSING`); reuse `rank_claims` / `assess_problem_text` / `_normalized`. `status = missing_result` when no results else `requires_user_selection`.
  - LLM path later behind `create_problem_space_detector` factory (flag `PROBLEM_SPACE_LLM_DETECTION`), following `create_story_synthesizer` (`services/story_synthesizer_factory.py`) exactly. Heuristic is the default this increment.
- **New `app/domain/bundle_validation.py`** (pure) — the 4 validators exactly per spec, as pre-generation gates:
  - `validate_problem_space_alignment(problem, action, result)` — one `problem_space_id` or `problem_space_mismatch`.
  - `validate_bundle_selection(bundle, selected_action_id, selected_result_id)` — both ids inside the bundle or `selected_{action,result}_outside_bundle`.
  - `validate_evidence_boundary(candidate)` — single `problem_space_id` (`cross_problem_space_contamination`) and ≤1 `project_id` (`cross_project_contamination`).
  - `validate_result_presence(bundle)` — non-empty `result_candidates` or `missing_result` + `next_action`.

### Increment 3 — Persistence: generalize stories to problem spaces
Turns Increment 2's derived spaces into first-class, selectable rows.

- **Migration `0014`** on `project_stories`:
  - Add `problem_space_id: str`, `problem_space_label: str|None`, `problem_space_scope: str|None`, `selected_action_id: str|None`, `selected_result_id: str|None`, `bundle_status: str`.
  - Replace `UniqueConstraint("experience_id")` (`models.py:309`) with `UniqueConstraint("experience_id", "problem_space_id")`. Backfill existing rows with a single derived `problem_space_id` per entity so v3 data survives.
- **`domain/project_story.py`** — `StoryContent` / `ProjectStory` gain `problem_space_id`, `problem_space_label`, `problem_space_scope`, `selected_action_id`, `selected_result_id`. `select_story_content` refactors to build **one `StoryContent` per detected problem space** (drive it from `detect_problem_spaces`); `actions_json`/`results_json` now hold the *candidate* lists for that space. `story_synthesis_fingerprint` (`:931`) keys per (experience, problem_space).
- **`services/story_synthesis.py`** — `run_story_synthesis` iterates detected spaces → upserts one draft per space (idempotency + quarantine unchanged). Repos (`InMemory*`/`Sql*ProjectStoryRepository`) updated for the composite key; `invalidate_story` cascade preserved.
- Keep the existing story→snapshot→docx render (`domain/story_snapshot.py`, `services/master_cv_render.py`) working: each story is now one clean problem space, which is the desired separation. `derive_bullets` still emits that space's actions/results — no render rewrite.

### Increment 4 — Selection + single-bullet generation (new path)
Fixes #4/#5 and the core invariant.

- **`services/story_review.py`** — add `select_bundle_component(story_id, selected_action_id, selected_result_id)`: runs `validate_bundle_selection` + `validate_problem_space_alignment` + `validate_evidence_boundary`; on pass persists `selected_action_id`/`selected_result_id` and sets `bundle_status=ready`. Reuse the existing attestation mechanism for `missing_result` follow-ups.
- **New `domain/bullet.py`** (pure) — `generate_bullet(bundle, selected_action_id, selected_result_id)`: hard-gates through all 4 validators, then composes exactly one bullet from the selected action + result (verbatim, number-grounded via existing `unsupported_number_tokens`). Refuses on any validator failure or `missing_result` (no hallucination).
- **`api/stories.py`** — `POST /stories/{id}/select` (bundle/action/result) → 409 on validator failure; `POST /stories/{id}/bullet` → returns the single generated bullet or the `missing_result` follow-up (the 7-option targeted result-type question). Existing endpoints unchanged.
- **Targeted follow-up** — reuse `QuestionKind.MISSING_RESULT` (`project_story.py`); add the 7 result-type options as the follow-up payload when `validate_result_presence` fails.

### Increment 5 — Expected-project reconciliation (independent)
Fixes #6.

- **New `domain/project_reconciliation.py`** (pure) — `reconcile_expected_projects(expected_inventory, confirmed_entities, raw_source_texts) -> list[ReconciliationResult]`. For each expected project: matched confirmed entity (name/alias, reuse `Experience.matches_name`) → `detected_in_resume: true`; else present in raw source but not parsed → parsing-gap status; else → `{"detected_in_resume": false, "status": "missing_from_resume_or_source_not_loaded", "next_action": "search_project_sources_or_ask_user_to_add"}`. Returns the exact spec JSON shape; never silently omits.
- **`services/roster.py`** — thin `run_project_reconciliation` wrapper over confirmed roster + gathered source docs; log to `validation_runs` (new kind `project_reconciliation`).

### Increment 6 — Eval invariant
- **`domain/evaluation.py`** — extend `summarize_story_eval` invariants with `cross_problem_space_contamination_count` (must be 0; folds into `boundary_clean`), so a mixed-space bullet is a hard regression on the scorecard.

---

## Tests (regression, over new fixtures)

Add a dedicated Cooper.ai fixture with the **real** FedEx/Pacifica/dataset text (the live-corpus `Cooper.ai` spec, `tests/live_corpus.py:171`, is synthetic `topic="onboarding"` and unusable for this) — a small module `tests/fixtures/problem_spaces/cooper_ai.py` holding resume text with the 6 result phrases + FedEx and Pacifica problem/action text.

1. `test_cooper_result_extraction.py` — result extractor over Cooper.ai text surfaces ≥ several of the 6 results as `result_candidates`, not all as actions (covers `$4.01M→$2.16M`, `195K+`, `100% coverage`, `five … datasets`, `daily … refreshes`).
2. `test_problem_space_separation.py` — FedEx + Pacifica text → separate problem spaces/bundles; no generated bullet combines FedEx problem/result with Pacifica action/result.
3. `test_bundle_boundary.py` — `validate_bundle_selection` with `selected_bundle_id="cooper_fedex_integrity_b1"`; an action/result from another bundle fails validation.
4. `test_project_reconciliation.py` — expected `paper_recommender_system` absent from parsed text → the exact reconciliation JSON; not classified as a parsing failure.
5. `test_missing_result.py` — bundle with problem + action, no result → `status: missing_result`, `next_action: ask_targeted_followup`; no hallucinated result.

Plus: extend `tests/test_story_eval.py` for the new contamination invariant, and keep `tests/test_full_v3_loop.py` / `tests/live_corpus.py` guards green through the migration/backfill.

---

## Likely failure points to watch
- **Migration backfill** — every existing `project_stories` row must get a deterministic `problem_space_id` or the new composite unique constraint / `story_synthesis_fingerprint` skip breaks idempotency. Backfill in `0014`.
- **Render coupling** — `story_master_cv_from_snapshot` and `create_story_snapshot` assume one story per entity in a few spots; verify per-space stories still snapshot and dedupe (`DuplicateMetricError` unchanged).
- **Over-broadening `_is_outcome`** — widening result recognition must not reclassify genuine *actions* as results; the strengthened `_classify` and the unchanged verbatim `outcome_quote` grounding bar are the guardrails. Add a negative case (a pure work statement stays `result_kind=missing`).

---

## Verification
- `uv run pytest -q` — full suite green offline, zero credentials (esp. the 5 new tests + `test_full_v3_loop` + `test_live_corpus` guards).
- `uv run ruff check . && uv run ruff format . && uv run mypy app` — required pre-commit gates (per CLAUDE.md).
- `alembic upgrade head` on a seeded DB, then re-run `run_story_synthesis` over `build_live_corpus()` and assert multi-space entities produce multiple stories while single-space entities are unchanged (no v3 regression).
- Drive the new path end-to-end in a test: detect spaces → select bundle → select one action + one result → `generate_bullet` returns one grounded bullet; selecting cross-bundle ids → 409.
