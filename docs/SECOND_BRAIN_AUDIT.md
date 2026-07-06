# Second Brain + Obsidian Architecture Audit

## 1. Executive verdict

**This is not a greenfield design — it is largely an audit of already-shipped code.** Roughly 65–75% of the doc's "JobPilot half" is built and merged. The entire spine the doc proposes in §5/§6 — Source → Evidence → Claim inventory → Project Story → readiness gate → human approval → output adapters — exists as `app/domain/project_story.py`, `claims.py`, `roster.py`, `par_validation.py`, `master_cv_snapshot.py`, `evaluation.py`, and the Alembic schema through migration `0013`. Verification confirmed this concretely: `compute_readiness`, `approval_blockers`/`assert_approvable`, `rank_claims`, `select_story_content`, `validate_story_structure`, `results_address_problem`, `detect_metric_conflicts`, and `detect_entity_overlaps` are all present and pure. The doc's headline "critical modeling correction" (separate readiness from approval) is not a correction to make — it is the shipped design (`review_status` is the only lifecycle column; readiness is a read-time struct with **no column**).

The doc's roadmap Phases 0–2 map 1:1 onto merged milestones **M16, M17, M18**. Presenting them as future work is the single biggest framing error; acting on it would risk a rebuild that discards migration `0013` and the live-corpus-tested gates.

**The doc's real value is the delta**, concentrated in four areas that genuinely do not exist in the repo: (1) the **Obsidian/Second-Brain ingestion substrate** (frontmatter/wikilink parsing, vault-zone policy); (2) the **sensitivity/disclosure model** (§11.1 — the one net-new *content-level* guardrail, filling a real hole the source-scoped policy gate misses); (3) the **cost/retrieval-snapshot runtime layer** (pre-flight token estimate, fail-closed budget ceiling, RetrievalSnapshot lock); and (4) the **V4 paste-a-posting → optimized-resume dashboard**. Everything downstream of those (render, number gate, approved-only snapshot, two-stage scoring engine) is reuse.

**Headline recommendation: build the Second Brain repo-native; reject the Graphiti fork.** Graphiti mandates a Neo4j/FalkorDB graph store as its primary backend, which directly violates the non-negotiable "Postgres is the transactional source of truth — never a warehouse for app data," defaults to OpenAI against the Anthropic-only LLM layer, has no offline mock path, and would re-implement entity/evidence/provenance modeling the repo already ships. Its one genuine differentiator (bi-temporal + semantic retrieval) is addable in-repo via pgvector without a second datastore.

## 2. Reality check: proposed vs already built

| Doc proposes | Status | Existing in repo | Evidence |
|---|---|---|---|
| §5/§6 layered architecture (Source→Evidence→Claim→Story→Readiness→Approval→Output) | **ALREADY BUILT** | The full spine as pure domain code | `app/domain/project_story.py`, `claims.py`, `roster.py` |
| §6 resume-ready hard gate (1 Problem + ≥1 Action + 1 Result) | **ALREADY BUILT** | `approval_blockers` / `assert_approvable` | `project_story.py:440,452` |
| §15 "separate readiness from review_status" | **ALREADY BUILT** | `review_status` sole lifecycle enum; readiness derived, no column | `project_story.py:60,270,298`; `models.py:305`; `0013` |
| §15.2 `project_story_readiness` table | **CONFLICT** | Readiness is a read-time struct, "Never persisted as a scalar status" | `project_story.py:270`; `models.py:305`; `0013` docstring |
| §15.1 `sources` | **ALREADY BUILT** | `cv_sources` (deduped provenance registry) | `models.py:62` |
| §15.1 `source_chunks` | **ALREADY BUILT** | `evidence` (chunk_text + `#chars=start-end` span refs) | `models.py:223`; migration `0010` |
| §15.1 `entities` | **ALREADY BUILT** | `experiences` (kind, status, `merged_into_id`) | `models.py:192`; `0010` |
| §15.1 `entity_aliases` | **ALREADY BUILT** | `experiences.aliases` JSON **column** (not a table) | `models.py:192` |
| §15.1 `memories` | **ALREADY BUILT** | `claims` (single-row typed PAR) | `models.py:251` |
| §15.1 `memory_evidence` / `attestations` | **ALREADY BUILT** | `claim_evidence` (field-per-link, `outcome_quote`); attestation = `SOURCE_USER_ATTESTATION` evidence | `models.py:278`; `claims.py:141` |
| §15.2 `project_capsules` | **ALREADY BUILT** | `project_stories` (one per entity, `experience_id` UNIQUE) | `models.py:299`; `0013` |
| §15.2 `problem/action/result_candidates` (3 tables) | **CONFLICT** | The columns of one `claims` row; splitting shatters `resolves` coupling | `models.py:251`; `select_story_content` |
| §15.2 `project_story_reviews` / `approved_project_stories` | **ALREADY BUILT** | `review_status` values on one row (`reviewed_at`, `decision_note`) | `models.py:314,322` |
| §15.2 `master_cv_entries` | **ALREADY BUILT** | `master_cv` versioned approved-claims snapshot | `models.py:48` |
| §15.2 `resume_bullet_candidates` | **ALREADY BUILT** | `TailoredMaterials.highlights` + `highlight_claim_ids` | `tailoring.py:66` |
| §15.1 `duplicate_groups` (as canonical table) | **ALREADY BUILT** (as pass) | `detect_entity_overlaps` + evidence uq `(user,source_type,source_ref)`; logged to `validation_runs` | `roster.py:196`; `models.py:233` |
| §16 Phase 0 (stop slop: fallback deleted, flag split) | **ALREADY BUILT** (M16) | Extraction refuses without confirmed roster; `STRUCTURAL`/`ABSENCE` split | `claim_extraction.py:330`; `par_validation.py:53,62`; `0012` |
| §16 Phase 1 (one-entity-once, cross-entity dedupe, unassigned queue) | **ALREADY BUILT** (M17) | `detect_entity_overlaps`, `run_overlap_detection`, `list_unassigned_evidence`, `POST /roster/evidence/{id}/assign` | `roster.py:196`; `api/roster.py:176` |
| §16 Phase 2 (story domain: readiness, questions, ranking, gates) | **ALREADY BUILT** (M18) | Full `project_story.py` + repos + migration `0013` | `project_story.py:298`+ |
| Appendix B item 5 — questions for missing Problem/Action/Result | **PARTIAL** | `MISSING_PROBLEM`/`MISSING_RESULT` derived; **no `MISSING_ACTION` question** exists (only a `COMPONENT_ACTIONS` gap marker) | `project_story.py:232,382` |
| Appendix B item 6 — server-side approval checks (block incomplete) | **PARTIAL** | Pure gate exists; **no API 409, no render refusal** — `assert_approvable` has no callers outside domain+tests | `project_story.py:440`; no `app/api/stories.py` |
| §7 / Phase 3 — one card per project, review UX | **PARTIAL** | One-per-entity domain object + repos exist; **no `api/stories.py`, no synthesis service, no web card** | `models.py:299`; globs absent |
| Appendix B item 7 / Phase 4 — snapshot from approved *stories* | **PARTIAL** | Approved-only enforced for **claims**; story-shaped snapshot + render-time P/A/R gate + dup-metric refusal **not built** | `master_cv_snapshot.py:70` |
| Appendix B item 11 — "LLM synthesis last, credit-gated" | **ALREADY BUILT** (as plan) | Committed in `ARCHITECTURE_V3.md §7 Phase 5`; heuristic selects, never authors | `docs/ARCHITECTURE_V3.md` |
| §13 / V4 — pasted posting → requirement parse → per-story scoring → optimized resume | **PARTIAL / NEW** | Two-stage engine scores **whole jobs vs aggregate CV**, not per-project vs a paste; no paste UI, no requirement extractor, no `optimized_resume_snapshot` | `matching.py:145,192`; `web/` has no paste surface |
| §11.1 sensitivity/disclosure model | **NEW** | No content-sensitivity code; only `github_include_private` (a source-scope flag) | `config.py:86`; `source_policy.py:73` |
| §10.2–10.6 / §15.1 cost + `retrieval_snapshots` / `career_project_index` | **NEW** (partly **CONFLICT**) | `CostTracker` logs only (no ceiling); no token estimate, no dedupe-by-content-hash, no snapshot object. A materialized index in Postgres would breach the warehouse rule | `llm/cost.py`; grep: none |
| §14 / §16 Phase 7 — fork Graphiti | **CONFLICT** | Violates warehouse rule, no-fork constraint, Anthropic-only, mock-first | `CLAUDE.md`; `models.py` (all SQLAlchemy) |
| §20 Obsidian-first MVP ("don't depend on Drive/GitHub connectors") | **CONFLICT** | Would re-route shipped, live-verified `McpDriveClient`/`McpGitHubClient`/uploads through a nonexistent staging layer | `integrations/mcp/drive.py`; `services/roster.py:97` |

## 3. Build vs fork: the Graphiti decision

**Decision: build the Second Brain repo-native in Postgres. Reject the Graphiti fork outright.** This is not a close call, and the reasoning is independent of the user's stated preference — the constraints already force it.

1. **It breaks the load-bearing invariant.** CLAUDE.md states verbatim: *"Postgres is the transactional source of truth — never a warehouse for app data."* Graphiti's primary backend is a graph database (Neo4j 5.26+, with FalkorDB/Neptune/deprecated Kuzu as the only alternatives); pgvector cannot serve as its primary store. Forking it stands up a second app-data store beside Postgres — precisely the warehouse the rule forbids. The repo has zero graph-DB dependencies today and all 13 migrations (`0001`–`0013`) target the SQLAlchemy/Postgres schema.

2. **It would re-implement what already ships.** The four things the doc says to "add to Graphiti" — typed memory extraction, approval/attestation states, app-scoped modeling, and Project Story integration — all exist: `claims` extraction, `ClaimStatus`/`ResultStatus.USER_ATTESTED`, and the whole `project_story.py` layer. Graphiti's entity/evidence/temporal modeling maps directly onto `experiences` (aliases, kind, status, `merged_into_id`), `evidence` (`source_ref`, char spans, `normalization_version`), and `claim_evidence`. Forking means re-porting all of it into a foreign graph model and discarding migration `0013`, the PAR validator, the overlap pass, and the number gate.

3. **It fights three other conventions at once.** Graphiti defaults to **OpenAI** for LLM + embeddings, against the Anthropic-only, cost-logged `app/llm/` layer. Its **automatic LLM entity extraction** reintroduces exactly the un-gated auto-entity pattern V3 Phase 0 deleted ("Roster mode is the ONLY mode" — a human confirms entities before extraction). And it has **no in-process fake**: it requires a running graph container to do anything, violating "the full pipeline runs offline before any real credential is required." A Graphiti-backed `domain/` would also breach the purity rule (`domain/` imports no I/O).

4. **Its one real advantage is cheaply replicable in-repo.** Graphiti genuinely offers bi-temporal reasoning and hybrid semantic+graph search that the repo lacks (today retrieval is deterministic token-overlap in `matching.py`). But **pgvector on Postgres** gives embeddings/semantic search without a second datastore, and an edges table with `valid_from`/`valid_to` columns gives temporal queries. Build the Career Index + RetrievalSnapshot layer in-repo behind a repository interface with an in-memory fake.

Supersede §14/§19 "Fork Graphiti" in the Decision Register with a **build-own** decision.

## 4. Critical & high findings

### CRITICAL

**C1 — `project_story_readiness` as a table is a regression, not a gap (§15.2).**
*Doc:* lists `project_story_readiness` as a table ("derived readiness struct"). *Reality:* readiness is deliberately **not** persisted — `compute_readiness` (`project_story.py:298`) returns `StoryReadiness` recomputed at read time; its docstring says *"Never persisted as a scalar status,"* the model comment and `0013` both say readiness *"deliberately has no column."* A stored scalar would let a stale value impersonate the recomputed truth and re-introduce the machine-readiness-vs-human-approval conflation the design was built to prevent; the compound-gap case (missing Problem AND Result) is only representable as the derived `missing` tuple. *Fix:* **Reject the table.** Expose readiness in the API response payload, never in a row.

**C2 — Forking Graphiti conflicts with the warehouse rule and the no-fork constraint.** See §3. *Fix:* build repo-native; label §14/§19 "conflicts-with-existing" and supersede with a build-own decision.

**C3 — Efficacy-overclaim and sensitive-leakage guards must be CODE gates, not prose (§11.1).**
*Doc:* states the rules narratively — *"personal outcome evidence is not product efficacy evidence"*; *"the app helps people get sober"* is unsafe unless clinically validated — with no enforceable predicate. *Reality:* the repo proves outbound safety is enforced by pure fail-closed predicates: `unsupported_numbers` (`tailoring.py:52`) and `unsupported_number_tokens` (`project_story.py:681`) reject ungrounded numbers, and `validate_story_structure` quarantines. But *"helps people get sober"* carries **no number**, so the number gate never catches it. This is the exact V1-bypass class of failure the doc's own §4.2 condemns. *Fix:* add a pure sibling predicate `disclosure_violations(text, sensitivity_class, disclosure_mode, output_type)` in `domain/`, wired at the **same three chokepoints** the number/resume-ready gates occupy: (1) story approval (`assert_approvable` → API 409), (2) render refusal in `services/master_cv_render.py`, (3) the tailoring/outreach outbound path in `llm/drafting.py`, failing LLM prose closed to heuristic. The efficacy check is a lexical/semantic gate on clinical-efficacy verbs + causation claims, unlocked only by a validated-efficacy attestation.

### HIGH

**H1 — The doc re-proposes the entire built spine as new work (§5/§6/§7).** Every layer, the resume-ready gate, and the readiness/approval separation are shipped. The "148 claims → project cards" review UX (§7) is the repo's already-scoped next milestone (M18 "built; awaiting review" → Phase 3), consuming `select_story_content`/`compute_readiness`/`rank_claims` verbatim. *Fix:* rewrite §5/§6/§7 as a description of the shipped architecture citing real symbols; scope the remaining work to the Phase 3 API/cards surface only.

**H2 — §15.2 candidate/readiness/review multi-table blueprint re-expands what `0013` deliberately collapsed.** `project_capsules`=`project_stories`; the three `*_candidates` tables are `claims` columns; `project_story_reviews`/`approved_project_stories` are `review_status` values on one row. Building it regresses migration `0013` and shatters the single-row PAR invariants. *Fix:* map to existing tables; add no new story/claim tables.

**H3 — Second Brain "core" tables (§15.1) duplicate `sources`/`evidence`/`experiences`/`claims`.** See the §2 table rows. *Fix:* treat §15.1 as already-built; if non-career memory types are ever needed, widen existing tables (a `kind` on `claims` or a sibling keyed identically), not a parallel graph.

**H4 — `career_project_index` / materialized retrieval index collides with the warehouse rule (§10.5/§15.2).** A denormalized index of derived signals inside Postgres is the warehouse pattern the rule forbids and duplicates what `job_matches` already derives per `(user, master_cv_version, job)`. *Fix:* keep the Career Index **outside** transactional Postgres — a sidecar (sqlite/pgvector artifact under `ARTIFACTS_DIR`) behind an interface with a mock.

**H5 — §11's flat 14-value "trust state" enum re-conflates four orthogonal axes.** It mixes lifecycle (`ClaimStatus`/`StoryReviewStatus`), verification (`ResultStatus.USER_ATTESTED` — a claim is simultaneously `PENDING_REVIEW` and user-attested, so they cannot be one enum), ingestion/cost status, and sensitivity. This is the exact conflation the doc's own §4.2 calls its "critical correction." *Fix:* keep the axes separate — lifecycle stays `ClaimStatus`/`StoryReviewStatus`; verification stays `ResultStatus`; ingestion status rides on `ingestion_runs`/`source_inventory`; sensitivity is its own orthogonal axis.

**H6 — The Obsidian-first MVP would regress shipped, live-verified connectors (§20).** *Doc:* "Jobpilot should NOT depend on direct Drive/GitHub/chat connectors for the MVP." *Reality:* `McpDriveClient` (`integrations/mcp/drive.py`) and `McpGitHubClient` are full, live-verified (M6) adapters feeding the single `gather_source_documents` path (`services/roster.py:97`). Re-routing everything through a nonexistent markdown staging layer re-architects a tested flow. *Fix:* Obsidian is an **optional additional source adapter** behind the existing interface pattern, not the mandatory substrate.

**H7 — Two divergent design docs plus a code stub that follows neither.** The Downloads synthesis says "Fork Graphiti" (§14) + new `obsidian_*` tables; the in-repo `docs/SECOND_BRAIN_ARCHITECTURE.md` says repo-native with an `integrations/obsidian.py` adapter. The untracked stub (`app/second_brain/`) has neither an adapter, frontmatter parsing, nor an API layer, and its `MemoryStatus(draft/reviewed/approved/rejected/archived)` is a **third** lifecycle vocabulary. *Fix:* adopt `docs/SECOND_BRAIN_ARCHITECTURE.md` as canonical (it matches the hard constraint), explicitly reject §14's fork, and salvage only the §20.2 frontmatter contract + §20.1/20.3 vault-zone allowlist as source-policy inputs.

**H8 — The divergent stub is a parallel pipeline, not an adapter.** `app/second_brain/services/vault_ingestion.py::VaultIngestionService.ingest` returns `list[NoteRecord]` that dead-ends — it never produces a `SourceDocument`, never calls `gather_source_documents`, never creates evidence, and `approve`/`reject` mutate in-memory only (no persistence, no migration). `NoteRecord.from_path` does `read_text` (**I/O in a domain dataclass**) and derives identity from the filename (`note:{stem}`) — the exact "files-treated-as-experiences" anti-pattern the roster layer eliminated. It also skips `normalize_source_text`, bypassing `NORMALIZATION_VERSION`. *Fix:* see §6.

## 5. The delta: what to actually build

### Build (genuinely new)

1. **Obsidian source adapter** — `ObsidianClient` Protocol in `integrations/base.py` (mirroring `UploadsClient`) + `LocalObsidianClient` + mock + `tests/fixtures/obsidian/`. Parse YAML frontmatter (`project_id`, `sensitivity_mode`, `jobpilot_ingest`) and wikilinks; `gather_source_documents` emits `SourceDocument(source_type=SOURCE_OBSIDIAN)`. `project_id` maps to a confirmed roster entity; the allowlist maps to an `apply_*_policy` gate. Model notes as `cv_sources` rows so they flow through roster→chunk→claims→story unchanged.
2. **Sensitivity/disclosure axis (§11.1)** — the one net-new *content-level* guardrail. Add `sensitivity_class` + `disclosure_mode` (`technical_only|domain_context_allowed|personal_origin_allowed|private_do_not_use`; default `technical_only`, sensitive-origin default `private_do_not_use`) to `project_stories`, plus a small `disclosure_approvals` table keyed `(experience_id, output_type)` holding revocable, timestamped per-artifact grants. The `disclosure_violations` predicate (C3). Model disclosure grants as **scoped attestations flowing through the approved-only gate** — never a parallel prose channel. For `redacted_promoted`, produce a `SOURCE_USER_ATTESTATION` evidence row holding only sanitized text and require redacted components to cite the attestation exclusively (else the grounding walk lands on the raw sensitive chunk one hop away).
3. **Cost/ingestion runtime controls** — the three that add real safety: pre-flight **token/cost estimate**, a **fail-closed max-spend ceiling** (today `CostTracker` only logs — the one true enforcement gap), and whole-document **content-hash dedupe**. Add `ingestion_runs` (distinct from `validation_runs`) and, only when broad multi-source ingestion lands, `source_budget_policies`. Skip per-source dollar-band tables for a single-user MVP.
4. **RetrievalSnapshot layer** — a locked, per-job snapshot object (as a table or sidecar) formalizing what the pipeline already does (`run_application_pipeline` reads only the approved snapshot; LLM stages carry no search tools). New: the formal lock + refresh + no-runtime-raw-search enforcement.
5. **Missing-Action question** — add a `MISSING_ACTION` `QuestionKind` and a `_derive_questions` branch for `actions == 0`. Today that gap only surfaces as a `COMPONENT_ACTIONS` marker, so a story with a Problem and zero Actions gets no targeted prompt. This is the *only* additive item in the readiness-question taxonomy.
6. **V4 paste-driven flow (§13)** — genuinely new: manual job-posting text input, a requirement parser, per-Project-Story role scoring (today `matching.py` scores whole jobs vs the aggregate CV, not projects vs one posting), and an `optimized_resume_snapshot`. Reuse the two-stage engine and docx render; `CareerContextView` is a thin read-only facade over existing repos. A pasted posting is a new `jobs` **source value** (e.g. `manual_paste`), not a new table.

### Do NOT rebuild (already shipped)

- The Source→Evidence→Claim→Story→Readiness→Approval spine (`project_story.py`, `claims.py`, `roster.py`).
- The resume-ready gate (`approval_blockers`/`assert_approvable`).
- Readiness-as-derived-struct + `review_status` separation (do **not** add a readiness column/table).
- `sources`/`source_chunks`/`entities`/`entity_aliases`/`memories`/`memory_evidence`/`attestations` (= `cv_sources`/`evidence`/`experiences`/`aliases` col/`claims`/`claim_evidence`/attestation evidence).
- Cross-entity dedupe (`detect_entity_overlaps`) + unassigned-evidence queue.
- Leverage ranking, question derivation, metric-conflict detection, structural quarantine.
- Approved-only Master CV snapshotting (for claims), the number-factuality gate, `highlight_claim_ids` traceability.
- The live-corpus fixture (15 entities, 148 claims, the duplicate quote + conflicting-metric pair) and story exit tests.
- Drive/GitHub/uploads connectors + `source_policy` gates.

## 6. Restructuring recommendation

**The `app/second_brain/` stub: replace, don't extend.** Delete `VaultIngestionService` and `NoteRecord`. Their function is served by the existing pipeline the moment Obsidian becomes a source adapter. Replace with:

- `ObsidianClient` Protocol in `app/integrations/base.py` + `LocalObsidianClient` (real) + `MockObsidianClient` (fixture-backed) in `app/integrations/`. File reads live here, behind the interface — never in a domain dataclass.
- `gather_source_documents` (`services/roster.py`) gains an `obsidian_client` param and emits `SourceDocument(source_type=SOURCE_OBSIDIAN)`, running each note through `normalize_source_text` so it carries `NORMALIZATION_VERSION`.
- No new lifecycle enum. Notes become evidence; review state is the existing `ClaimStatus`/`StoryReviewStatus`. The stub's `MemoryStatus` must not ship.
- Frontmatter `project_id` routes note text to the matching confirmed roster entity (or the unassigned-evidence queue) — never keyed on filename.

**Canonical doc:** adopt `docs/SECOND_BRAIN_ARCHITECTURE.md` (repo-native, matches the hard constraint) as the single source of truth for this subsystem. Explicitly reject the Downloads synthesis's §14 Graphiti fork in the Decision Register. Salvage from the synthesis only the §20.2 frontmatter contract and §20.1/20.3 vault-zone allowlist, folded into `source_policy.py` as one adapter's policy inputs.

**Where new code lives** (extend existing patterns, add nothing parallel):
- Adapters → `app/integrations/` (`obsidian.py`, future `chat_export.py`) + `mock/` + `tests/fixtures/`.
- Ingestion/cost orchestration → `app/services/` (`ingestion.py` alongside `roster.py`); `CostTracker` ceiling in `app/llm/cost.py`.
- Sensitivity/disclosure predicates → `app/domain/` (pure, next to `unsupported_numbers`); repos in `app/db/` + `app/services/`.
- Schema → one additive Alembic migration `0014` mirroring `0013`'s nullable/`server_default` style; split by feature (`0014` sensitivity, `0015` cost-governance, `0016` retrieval) so each ships with its interface+mock. Never a data-destructive change to `project_stories`; never a `project_story_readiness` or `career_project_index` table.
- Career Index / retrieval index → sidecar under `ARTIFACTS_DIR` (optionally pgvector), behind a repository interface with an in-memory fake — not transactional Postgres.

## 7. Risks & guardrails worth keeping

These parts of the doc are genuinely valuable and should be preserved as first-class requirements:

- **Deny-by-default sensitivity classification** (§11.1) — the real content-level gap the source-scoped `source_policy` misses. A legitimately-ingested recovery-domain repo passes `apply_repo_policy` with zero downstream disclosure control today. Keep it — but as code gates at the three existing chokepoints (C3), anchored on the `project_stories` row, single-consumer (drop `permissions`/`app_context_views` as over-scoped).
- **Per-artifact, revocable disclosure grants** — never a global `disclosure_approved` flag; model as `(story, output_type)` timestamped rows, matching the repo's retained-per-object-decision convention (`RejectionReasonRequiredError`, `ExclusionReasonRequiredError`).
- **No runtime raw-source search** (§10.5) — already the architecture (snapshot-only generation, no search tools on LLM stages); formalize it as the locked RetrievalSnapshot so it stays true as new sources land.
- **Pre-flight dry-run cost audit + fail-closed budget ceiling** (§10) — the genuine enforcement gap; `CostTracker` logs but cannot stop a run.
- **One project → one card; readiness ≠ approval** — already shipped and load-bearing; the audit's job is to *protect* these from the doc's own §15.2 re-expansion.
- **LLM synthesis last, credit-gated** — already the committed plan (`ARCHITECTURE_V3.md §7 Phase 5`); heuristic selects, LLM only authors prose after gates pass and one `eval_stories`-scored live run.

## 8. Recommended MVP + phased sequence

Dovetail with the **existing** tracker (M18 = V3 Phase 2 built, awaiting review), not the doc's greenfield Phase 0–9. Smallest first useful slice first; new Second-Brain work is strictly downstream of a working approved-story → Master CV path.

| Phase | Slice | Scope | Depends on |
|---|---|---|---|
| **0** | Land M18 review | Merge the built story domain layer + repos + migration `0013` | — |
| **1 (next)** | **V3 Phase 3 — story review API + cards** | `app/api/stories.py` (server-side **409** via `assert_approvable`, structural answer validation, component edit-attest, exclude-with-reason); heuristic `story_synthesis` service (hash-skip/quarantine); web card UI rendering evidence quotes under each component. Additive to the claims queue until exit tests pass. **The single highest-leverage unbuilt thing** — the gate exists but is enforced zero times at runtime. Add the `MISSING_ACTION` question here. | Phase 0 |
| **2** | **V3 Phase 4 — render from approved stories** | Story-shaped `build_snapshot_content` consuming approved complete stories; render-time P/A/R gate + duplicate-metric refusal in `services/master_cv_render.py` | Phase 1 |
| **3** | **V3 Phase 5 — adapters + outreach gate + LLM synth** | Story→MasterCv adapter, outreach-body number gate; then the LLM `StorySynthesizer` behind a flag, fake-client-tested, **one live run last**, credit-gated | Phase 2 |
| **4** | **Sensitivity/disclosure model (§11.1)** | Migration `0014`: `sensitivity_class`/`disclosure_mode` on `project_stories` + `disclosure_approvals`; `disclosure_violations` predicate wired at the three chokepoints; redacted-promotion attestation rule | Phase 2 (needs render gate) |
| **5** | **Obsidian source adapter** | `ObsidianClient` + mock + fixtures; frontmatter/wikilink parsing; notes as `cv_sources`/`SOURCE_OBSIDIAN` through `gather_source_documents`. Replaces the stub. Optional, additive — Drive/GitHub/uploads stay primary | Phase 1 pipeline |
| **6** | **Ingestion cost controls** | `CostTracker` fail-closed ceiling; pre-flight token estimate; content-hash dedupe; `ingestion_runs`. Only when broad multi-source (chat exports) actually lands | Phase 5 |
| **7** | **V4 paste-driven optimized resume** | `manual_paste` job source, requirement parser, per-story role scoring, `optimized_resume_snapshot`, paste UI. Reuse two-stage engine + docx render | Phase 3 |
| **8** | **RetrievalSnapshot + Career Index (sidecar)** | Formal per-job snapshot lock; pgvector/sidecar semantic retrieval — Graphiti's real value, repo-native | Phases 6–7 |

Scraping / job-board mining / company research stays **v5 / out of scope**, consistent with existing policy. Do not front-load a greenfield cost or Second-Brain program — Phases 1–3 need none of it and must not wait on it.