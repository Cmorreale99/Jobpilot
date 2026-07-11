# Pre-V4 Pipeline Hardening — Verified Architecture Review & Execution Plan

**Date:** 2026-07-11
**Status:** definitive. Supersedes the milestone plan in `PIPELINE_LOSS_AUDIT.md` §7 (the audit's findings are re-verified here; its H1–H6 plan is replaced by the roadmap in §3).
**Method:** every finding below was re-verified against the working tree this session by direct code reading — not carried forward from the earlier multi-agent audit. Verdicts state what was confirmed, what was nuanced, and what the earlier audit missed.
**Rule of the project:** feature work is frozen. Nothing in this plan adds product capability; every milestone exists to make the source→evidence pipeline deterministic, lossless, auditable, and testable before V4.

---

## 1. Phase 1 — Verified findings

Confidence legend: **CONFIRMED** = code read this session, mechanism reproduced from source; **CONFIRMED-** = confirmed with a correction to the earlier statement.

### F1 — No canonical source capture exists (CONFIRMED, critical)

**Evidence.** `gather_source_documents` (`app/services/roster.py:118-190`) fetches each source, immediately wraps it in `normalize_source_text(...)` (`:146, :158, :170, :186`), and returns in-memory `SourceDocument`s. No repository is touched. `CvSourceRow` (`app/db/models.py:62-79`, includes `raw_text`) has zero writers or readers outside its definition (verified by repo-wide grep). `app/services/master_cv_ingestion.py`, still referenced in CLAUDE.md, does not exist.
**Root cause.** V1's ingestion service was deleted in the audit Phase 3 cleanup; the V2 roster path was built as a pure in-memory gather and no capture layer replaced the dead table.
**First durable write of any source text:** `evidence.chunk_text` (`app/db/models.py:240`) at roster assignment — post-normalization, post-chunking, post-assignment.
**Affected:** `app/services/roster.py`, `app/db/models.py`, migration chain. Tables: `cv_sources` (dead), `evidence`.
**Regression risk of fixing:** low (additive). **Complexity:** medium (new table + gather wiring).

### F2 — Machine re-runs destroy human evidence assignments (CONFIRMED, critical)

**Evidence.** `run_roster_assignment` calls `repository.assign_evidence(stored.id, target)` unconditionally for every chunk — commits at `app/services/roster.py:369`, chunked docs at `:389` — where `target` is the fresh machine guess. `assign_evidence` (`app/db/claim_repository.py:302-310`) blindly sets `row.experience_id`. The `evidence` table (`models.py:223-248`) has **no column recording how an assignment was made** — a human correction via `POST /roster/evidence/{id}/assign` (`app/api/roster.py:179-200`) is bitwise indistinguishable from a heuristic guess, so the next `/roster/assign` run overwrites it.
**Test gap confirmed:** grep of `tests/` shows no test running `run_roster_assignment` after a manual `assign_evidence` and asserting survival (`tests/test_roster_service.py:312` re-runs assignment but never pins a human decision first).
**Root cause.** Ownership is stored as a bare FK with no provenance-of-decision; the "human decisions are never overwritten" invariant was implemented for claims and stories but never for evidence assignment.
**Affected:** `app/services/roster.py`, `app/db/claim_repository.py`, `app/domain/claims.py` (in-memory repo), `app/api/roster.py`. Tables: `evidence`.
**Regression risk:** low. **Complexity:** low (column + guard + method arg).

### F3 — Structure is destroyed before ownership is decided; ownership is then guessed (CONFIRMED, critical — the Cooper mechanism)

**Evidence chain, all read this session:**
- Flattening #1: real Drive extraction happens inside the MCP server (`app/integrations/base.py:82-83`; `app/integrations/mcp/drive.py:252-260`); no PDF/DOCX parser exists in the repo (`python-docx` is render-side only, via `docxtpl`). GitHub keeps README.md + commit message text only.
- Flattening #2: `normalize_source_text` (`app/domain/text_normalization.py:61-87`) reflows lines, collapses whitespace, and heuristically deletes "soft" blanks (`_blank_is_soft`, `:54-58`); its input is discarded (F1).
- Chunking is structure-blind: `chunk_normalized_text` (`app/domain/chunking.py:74-82`) yields paragraph chunks with exact spans into normalized text, but no heading membership, no adjacency, no persisted order; an oversized paragraph splits mid-content (`_split_oversized`, `:52-71`), able to sever a Problem from its Result.
- Assignment then re-infers ownership context-free: `ChunkAssigner.assign(chunks: Sequence[str], roster)` (`app/domain/roster.py:97`) receives **bare strings** — no document, no neighbors, no headings. The heuristic (`HeuristicChunkAssigner`, `:149-172`) is pure name/alias token overlap (argmax, ties refused); the LLM assigner sees each chunk truncated to 800 chars (`app/llm/roster.py:38, :202`). README chunks are force-assigned to the repo entity with zero content check (`app/services/roster.py:375-377`).
- A wrong-but-confident assignment is **fully silent**: it isn't a tie (→ unassigned queue), isn't an exact duplicate (→ overlap prompt, `app/domain/roster.py:196-256` matches exact normalized text only), and isn't an LLM error (→ 502). It simply feeds the wrong entity's extraction.
**Test gap confirmed:** no test reproduces a chunk assigned to the wrong entity via boundary split or lexical overlap (the Cooper shape). `tests/fixtures/problem_spaces/cooper_ai.py` covers problem-space separation *after* correct assignment.
**Root cause.** Ownership and hierarchy — source data in the document — are destroyed at flatten/normalize/chunk, then reconstructed by inference at assignment. This is the architectural inversion the whole project exists to fix.
**Affected:** `app/domain/chunking.py`, `app/domain/text_normalization.py`, `app/domain/roster.py`, `app/llm/roster.py`, `app/services/roster.py`. Tables: `evidence`.
**Regression risk of fixing:** medium (assigner interface + behavior change — deliberate). **Complexity:** high (this is the substantive milestone).

### F4 — Normalization is irreversible in place and its version is unenforced (CONFIRMED)

**Evidence.** `NORMALIZATION_VERSION = 1` with a "BUMP THIS on ANY change" comment (`app/domain/text_normalization.py:33-38`); nothing in CI or tests detects a rule change without a bump. Span refs (`#chars=start-end`) are offsets into normalizer **output** (`app/domain/chunking.py:1-16`), which is never stored (F1). `_upsert_evidence_row` re-stamps the version only when chunk text happens to change under an identical span ref (`app/db/claim_repository.py:347-351`).
**Root cause:** invariant-by-comment. **Affected:** `text_normalization.py`, tests. **Risk:** trivial. **Complexity:** low.

### F5 — Evidence has no lifecycle: span changes orphan rows that keep feeding extraction (CONFIRMED)

**Evidence.** Evidence identity is `(user_id, source_type, source_ref)` (`models.py:232-234`) where `source_ref` embeds the char span — so any boundary shift mints **new rows**; `_upsert_evidence_row` (`claim_repository.py:339-362`) updates in place only on an identical ref. There is **no `delete_evidence`** anywhere in `app/` (verified by grep) and no tombstone/active flag. `list_assigned_evidence` (`:312-322`) filters only on `experience_id`, ordered by row id — stale generations feed extraction alongside fresh ones, in insertion order, with incomparable span coordinate systems when `normalization_version` differs.
**Root cause:** chunk identity = coordinates into an unstored, versioned text. **Affected:** repos, `services/roster.py`, `services/claim_extraction.py`. Tables: `evidence`, `claim_evidence`. **Risk:** medium (touches every evidence read). **Complexity:** medium.

### F6 — Per-source read failures are swallowed asymmetrically (CONFIRMED)

**Evidence.** Drive: `DriveResponseError` → `logger.warning` + `continue` (`services/roster.py:138-140`). GitHub README/commits: same pattern (`:161-162, :173-174`). Uploads: `read_upload` unguarded (`:180`) — an uploads failure aborts the whole gather. No disposition record anywhere; a failed source is indistinguishable from an empty one (the exact "Paper recommender: no evidence" confusion).
**Affected:** `services/roster.py`. **Risk:** low. **Complexity:** low (report + symmetric handling, lands with F1's capture layer).

### F7 — The story snapshot drops evidence provenance at the freeze (CONFIRMED)

**Evidence.** `_prepare_renderable` builds each component's `evidence_text` as `"\n".join(chunk_texts)` (`app/services/story_snapshot.py:80-89`) — per-chunk `source_type`/`source_ref`/`source_url`/`normalization_version` are not carried (only attested components keep a `story:{id}:{component}` ref, `:110`). Downstream consumers hold int `claim_ids` resolvable only while live rows exist.
**Affected:** `services/story_snapshot.py`, `domain/story_snapshot.py`. **Risk:** low. **Complexity:** low.

### F8 — Extraction batching silently severs Action↔Result pairs (CONFIRMED)

**Evidence.** `_split_group` (`app/llm/extraction.py:64-81`) splits groups at 30 chunks / 14K chars; "Results attach within their batch only — an outcome statement in another batch stays an honest missing Result" (comment `:54-56`). No per-pairing logging exists — only the batch count. The demotion to `missing` is invisible per-claim.
**Affected:** `llm/extraction.py`, `services/claim_extraction.py`. **Risk:** low. **Complexity:** low (counting + logging, not behavior change).

### F9 — Dropped claim drafts survive only as `action_text[:80]` (CONFIRMED)

**Evidence.** `app/services/claim_extraction.py:395-404`: the report keeps `f"{experience.name}: {draft.action_text[:80]}"`; `validation_runs` gets the violation strings, not the draft. The full problem/action/tools/result of a structurally-dropped draft is unrecoverable except by re-extraction.
**Affected:** `services/claim_extraction.py`. **Risk:** trivial. **Complexity:** trivial (persist draft JSON in the detail).

### F10 — Quote provenance is substring-match, not offsets (CONFIRMED)

**Evidence.** `verbatim_in` (`app/domain/par_validation.py:96-103`): whitespace-collapsed, case-sensitive substring containment — shared by validator and grounding filter. A quote appearing twice in a chunk has an ambiguous location; nothing stores offsets. Acceptable *within* a 1200-char chunk, but unverifiable against the original document once raw text is gone (F1 fixes the verifiability half).
**Affected:** none directly (fixed transitively by F1/F3 work). **Complexity:** n/a — accepted with provenance, see §5.

### F11 — Source metadata dropped at the `SourceDocument` boundary (CONFIRMED-)

**Evidence.** `SourceDocument` = `{source_type, source_ref, title, text}` (`app/domain/roster.py:42-49`). **Correction to the earlier audit's phrasing:** `title` *does* survive gathering; it is lost at evidence persistence (the `evidence` table has no title/mime/order/section columns, `models.py:236-248`). MIME, modified_time, size, language, commit authored_at are dropped at the dataclass boundary.
**Complexity:** trivial (lands with F1's capture table).

### F12 — LLM prompt truncations hide evidence from detection/assignment (CONFIRMED)

**Evidence.** `_DOC_TEXT_BUDGET = 4000`, `_CHUNK_TEXT_BUDGET = 800` (`app/llm/roster.py:37-38`). An entity evidenced only past char 4000 of a document is invisible to LLM roster detection; a chunk's disambiguating tail past 800 chars is invisible to LLM assignment. Both silent.
**Disposition:** the budgets are legitimate cost controls; the defect is silence. Fixed by F3's section-context design (the heading trail rides in the prompt regardless of body truncation) + logging truncation occurrences.

### F13 — Dashboard serializes story snapshots as zero claims (CONFIRMED)

**Evidence.** `app/api/dashboard.py:31` imports `master_cv_from_snapshot` (claim-shaped adapter) and calls it at `:45`; that adapter iterates `experience.get("claims", [])` (`app/domain/master_cv_snapshot.py:136-138`). Story-shaped snapshots have `actions`/`results`/`bullets`, no `claims` key → empty claim list. `master_cv_from_any_snapshot` (`app/domain/story_snapshot.py:253-262`) exists and dispatches correctly; the dashboard doesn't use it.
**Complexity:** one line + one test.

### F14 — Policy-rejected candidates are dropped without record (CONFIRMED, low)

**Evidence.** `apply_source_policy`/`apply_repo_policy`/`apply_upload_policy` (`app/services/source_policy.py:43-49, :79-85, :94-98`) are pure filter comprehensions; nothing records what was excluded or why. Intended behavior, wrong observability. Lands with F1's gather report.

### Findings the earlier audit got right that need no code change

The claim→story→approval→render segment is sound and is **out of scope for hardening**: LLM failures are loud typed errors with no silent fallback (`services/claim_extraction.py:380-393` et al.); human claim/story decisions survive every re-run (`replace_unreviewed_claims` skips reviewed fingerprints; `StoryReplaceError` protects decided stories); recorded problem-space partitions are first-write-wins; the number gates fail closed; the frozen renderer is a pure view of a frozen version.

---

## 2. Phase-1 verdict on the two design questions

### 2.1 Is storing pre-normalized text enough (M2/H2 sufficiency)?

**No — not by itself. But the answer is source-type-dependent, and that shapes the roadmap.**

- For **today's sources** (Markdown/plain-text Drive fixtures and MCP-extracted text, READMEs, commit messages, uploads), structure *is deterministically recoverable from raw text*: headings are `#`/underline lines, lists are bullet prefixes, paragraphs are blank-line blocks. Raw capture + a deterministic, versioned **structurer** (raw text → element tree) gives lossless structure without any parser we don't have.
- For **V4 sources** (PDF/DOCX), structure is **not** recoverable from extracted flat text — heading levels, table cells, list nesting, and page geometry exist only at parse time. Raw text capture alone would rebuild today's problem one layer down.

**Therefore: canonical capture must be two artifacts, not one** — the immutable raw payload (bytes/text as received) **and** a canonical `source_elements` tree. For text sources the tree is a *derived, reproducible* view (recomputable from raw, versioned by `STRUCTURER_VERSION`); for V4 PDF/DOCX the same schema is populated by the parser at ingest time, where the structure still exists. Designing the element schema **now** means the M23 parser plugs into an existing lossless spine instead of standing beside it — which is exactly the gap the exhaustive-ingestion spec leaves open (it never maps `SourceElement` onto the existing `evidence`/span model).

**How elements prevent the Cooper regression, mechanically.** Today the FedEx-results chunk is assigned by token overlap against entity names, alone, context-free (F3). With elements: the chunk derives from paragraph elements that sit under a heading element ("Cooper.ai — data engineering") in a persisted tree; assignment happens **per section subtree**, so the results paragraph inherits Cooper ownership from its section, no matter how few entity tokens its own text contains; a chunk boundary can never cross an element boundary, so a Problem and its Result under one heading cannot be split into differently-owned fragments; and re-ingestion re-derives the same tree from the same raw bytes (content-hash checked), so ownership is stable across runs instead of re-guessed.

### 2.2 Should ownership be persisted or reconstructed?

**Persisted — at the section level, inherited downward, with human pins immutable.** Reconstruction is still occurring today for exactly one reason: the information the assigner needs (heading membership, order, adjacency) has already been destroyed by the time assignment runs, so inference is the only option left (F3). That is an ordering bug in the architecture, not a modeling necessity. After hardening: ownership is decided once per section (machine-proposed, human-correctable), stamped onto every chunk in the subtree with its `assignment_method`, and never re-inferred for pinned rows (F2 fix). The per-chunk lexical assigner remains only as the fallback for genuinely structureless sources (e.g., a flat text file with no headings) — and even then its guess is labeled as a guess.

---

## 3. Phase 2 — Revised milestone roadmap

Changes from the prior plan (H1–H6): **H4 is redesigned** around the `source_elements` layer (heading-context-on-chunks was a half-measure — it fixed the prompt, not the model); **a P0 bugfix is pulled out** (one-liner, no reason to wait); **H8 is added** (end-to-end reconciliation + the V4 readiness gate — the prior plan had invariants but no enforcement milestone); ordering is otherwise preserved and re-justified below. Migration numbers continue from `0015`.

### P0 — Dashboard adapter bugfix *(ship immediately, independent)*

- **Purpose / problem:** F13 — `/master-cv/latest` renders story snapshots as 0 claims.
- **Files:** `app/api/dashboard.py` (import + call `master_cv_from_any_snapshot`); test in `tests/test_dashboard_api.py` with a story-shaped snapshot.
- **Migrations / API:** none / none (response becomes correct, not different in shape).
- **Acceptance:** a story-shaped snapshot serializes with its components as claims; legacy claim-shaped snapshots unchanged.
- **Risk:** trivial. **Rollback:** revert one commit.

### H1 — Human decision protection *(first substantive milestone)*

- **Purpose:** a human evidence assignment is a retained decision, symmetric with claim/story review. Stops the only *active* destruction of human work (F2).
- **Migration `0016`:** `evidence.assignment_method` (`String(16)`, nullable: `heuristic|llm|readme_ref|repo_ref|human|section` — `section` reserved for H5), `evidence.assigned_at` (nullable timestamp).
- **Changes:** `assign_evidence(evidence_id, experience_id, *, method)` across the protocol (`app/services/claim_repository.py:224`), SQL repo (`app/db/claim_repository.py:302`), in-memory repo (`app/domain/claims.py:935`); `api/roster.py:200` passes `human`; `run_roster_assignment` (`services/roster.py:369, :389`) labels its writes and **skips any row whose method is `human`**; README/commit paths labeled `readme_ref`/`repo_ref`.
- **API changes:** none externally; unassigned/overlap queue payloads gain `assignment_method` (additive).
- **Tests:** manual assign → re-run `/roster/assign` → assignment + method unchanged (the missing clobber test); each path stamps its label; merge preserves method; legacy NULL-method rows behave as machine rows.
- **Acceptance:** no machine write can change a `human` row; every new evidence row carries a method.
- **Dependencies:** none. **Risk:** low. **Rollback:** column is additive; revert code, leave column (harmless).

### H2 — Canonical source capture + loud gather

- **Purpose:** point of no return #1 exists: immutable raw payloads + full disposition accounting (F1, F6, F11, F14).
- **Migration `0017`:** new tables
  - `source_documents`: `id`, `user_id`, `source_type`, `source_ref` (unique per user+type), `title`, `mime_type`, `modified_time`, `size_bytes`, `created_at`.
  - `source_document_versions`: `id`, `document_id` FK, `content_hash` (sha256 of raw payload, unique per document), `raw_text` (as received — **pre-normalization**), `extractor` (e.g. `mcp:get_drive_file_content`, `local:read_text`), `fetched_at`, `is_active` (bool). New hash ⇒ new version row + prior deactivated; **raw rows are never updated or deleted** (immutability invariant).
  - Drop dead `cv_sources` in the same migration (its historical rows are empty by definition — no writer ever existed; verify `SELECT COUNT(*)` = 0 in the migration and refuse to drop otherwise).
- **Changes:** `gather_source_documents` writes/updates capture (idempotent by content hash) via an injected optional store (pure-offline callers unaffected); error handling made symmetric (uploads wrapped like Drive/GitHub); returns a **GatherReport** `{ok, read_failed(reason), policy_excluded(reason)}` persisted to `validation_runs` (kind `source_gather`). `SourceDocument` gains `mime_type`/`modified_time`/`size_bytes` fields.
- **API changes:** `POST /roster/detect` and `/roster/assign` responses gain the gather report summary (additive).
- **Tests:** raw text durable and byte-identical to client output; re-gather same content → same version row (idempotent); changed content → new version, old inactive but present; failing mock client lands in the report not the void; uploads failure no longer aborts the gather; policy exclusions recorded with reasons.
- **Acceptance:** for every subsequently-written evidence row, pre-normalization text is recoverable from the DB; `silently_dropped_sources = 0` — every discovered candidate has exactly one recorded disposition.
- **Dependencies:** none (H1 parallel-safe). **Risk:** low-medium. **Rollback:** tables are additive; gather report is additive; revert code, keep tables.

### H3 — Versioned derivations (normalization + structurer)

- **Purpose:** an un-bumped rule change is impossible to ship (F4); normalized text becomes a recomputable derivation of stored raw.
- **Migration:** none (stamp `normalization_version` onto `source_document_versions` rows at gather; column included in `0017`).
- **Changes:** golden-corpus digest test — `tests/fixtures/normalization_corpus/` (pathological inputs: word-per-line PDF debris, hyphen splits, wrapped bullets, real paragraph breaks) + a committed digest keyed by `NORMALIZATION_VERSION`; any rule change without a bump = red suite. Same pattern reserved for `STRUCTURER_VERSION` (H4). Add the missing edge-case tests: hyphen over-deletion (`state-\nof-the-art`), false soft-blank merge of two short real paragraphs — as *characterization* tests documenting current behavior.
- **Acceptance:** editing any rule in `text_normalization.py` without bumping the version fails CI; `normalize(raw_text)` reproduces the text downstream spans point into, for every active version row.
- **Dependencies:** H2 (raw must exist to assert recomputability). **Risk:** trivial. **Rollback:** delete tests.

### H4 — Canonical source elements (structure is source data)

- **Purpose:** persist document hierarchy as rows, not inference (F3 first half). The chunking/assignment substrate and the schema V4's PDF/DOCX parsers will populate.
- **Migration `0018`:** `source_elements`: `id`, `document_version_id` FK, `parent_element_id` (nullable self-FK), `sequence_index` (document order, unique per version), `element_type` (`heading|paragraph|list_item|code_block|blockquote|table|table_row|commit_message`), `level` (heading depth / list nesting, nullable), `raw_start`/`raw_end` (offsets into `raw_text` — verbatim slice invariant `raw_text[raw_start:raw_end] == raw_slice`), `normalized_text`, `content_hash`, `extraction_status` (`ok|unsupported|parser_error`), `note` (warning/error detail). Indexed on `(document_version_id, sequence_index)`.
- **Changes:** new pure module `app/domain/source_structure.py` — `structure_source_text(raw: str) -> list[SourceElement]`, deterministic, `STRUCTURER_VERSION = 1`, reusing/absorbing the structural-line detection already in `text_normalization.py` (`_starts_structure`); Markdown-aware (heading levels via `#` count, list nesting via indent), plain-text fallback (paragraph blocks). Gather populates elements per document version. **Reconciliation is structural:** the concatenation of element raw spans plus inter-element separators must cover `raw_text` exactly — full-coverage check, stronger than count-based reconciliation; a coverage gap marks the version `ingestion_status=failed` and blocks downstream synthesis for it.
- **API changes:** none yet (elements are substrate; surfaced via H5/H6 reports).
- **Tests:** golden element trees for fixture documents (Markdown with nested sections/lists, plain text, commit message); coverage invariant property test (`sum of spans + separators == len(raw)` over the corpus); structurer digest test keyed by `STRUCTURER_VERSION`; idempotent re-structuring (same raw → same tree, same hashes).
- **Acceptance:** every active document version has a full-coverage element tree; every element has explicit disposition (`ok|unsupported|parser_error`); zero unaccounted characters.
- **Dependencies:** H2, H3. **Risk:** medium (new substrate, but nothing consumes it yet — deliberately landed before the consumer switch). **Rollback:** table + module are additive and unconsumed until H5.

### H5 — Structure-aware chunking + section-scoped ownership *(the Cooper fix)*

- **Purpose:** ownership becomes persisted section-level source data, inherited by chunks; assignment stops guessing (F3 second half, F12's silence).
- **Migration `0019`:** `evidence` gains `element_id` (nullable FK → `source_elements`), `sequence_index` (nullable int), `section_path` (nullable text — the heading trail, e.g. `"Cooper.ai — data engineering > FedEx migration"`).
- **Changes:**
  - Chunking consumes elements, not flat text: a chunk = one element ≤1200 chars, or an element split at sentence boundaries — **never across element boundaries**. Span refs stay (back-compat) but identity moves toward `(content_hash of document version, element sequence)`.
  - Assignment becomes two-stage: (1) **section assignment** — one decision per top-level section subtree (heuristic: heading tokens vs roster, then body; LLM variant sees the heading trail + section summary, not isolated 800-char fragments); (2) chunks inherit the section's entity with `assignment_method='section'`. Per-chunk lexical assignment survives only for structureless sources, labeled `heuristic` as today. README force-assignment stays but is now *correct by construction* (the README's tree is one repo-owned document) and labeled.
  - Human corrections: `POST /roster/evidence/{id}/assign` continues to pin single chunks (`human`); new `POST /roster/sections/{element_id}/assign` pins a subtree (all descendant chunks stamped `human`). H1's guard protects both.
  - `list_assigned_evidence` orders by `(document_version, sequence_index)` — document order becomes a column, not a parse of `source_ref`.
  - Truncation events in LLM prompts logged + counted in the assignment report (F12).
- **API changes:** section-assign endpoint (new); roster assignment report gains per-section decisions; unassigned queue groups by section.
- **Tests:** **the Cooper regression fixture** — a multi-section document where one section's Result paragraph contains zero entity tokens: must inherit the section's entity, never `None`, never a lexically-similar wrong entity; boundary-split test — an oversized section chunked into many pieces keeps uniform ownership; heading-context beats vocabulary tie; structureless-source fallback still refuses ties; order round-trips through persistence.
- **Acceptance:** a chunk under a heading naming entity X can never be silently assigned to entity Y; Problem and Result within one section cannot receive different owners; document order reconstructable from columns for every post-H5 row.
- **Dependencies:** H1 (method labels), H4 (elements). **Risk:** medium-high — assignment behavior changes on purpose; gate with the live u1 corpus re-run compared against the current (human-corrected) assignments before switching the default. **Rollback:** the old flat-text assigner path is kept behind the factory for one release; flipping the factory back restores prior behavior without schema rollback.

### H6 — Evidence lifecycle: supersede, never orphan

- **Purpose:** re-ingestion visibly replaces evidence instead of accumulating live stale rows (F5).
- **Migration `0020`:** `evidence.is_active` (bool, default true), `evidence.superseded_by_id` (nullable self-FK).
- **Changes:** on assignment re-run, rows of a base ref absent from the fresh chunk set are marked inactive, pointing at the overlapping successor when determinable — **never deleted** (`claim_evidence` links stay intact). Extraction grouping, overlap detection, and the unassigned queue read active rows only. A reconciliation summary (active/superseded/new counts, mixed-version warnings) lands in `validation_runs` (kind `evidence_reconciliation`). A superseded row backing a **reviewed** claim is surfaced loudly in the report — the human decided on text that no longer exists upstream; never auto-resolved.
- **API changes:** roster report gains reconciliation summary; a `GET /roster/superseded-reviewed` surface (or a section of the existing report) lists reviewed-claims-on-stale-evidence.
- **Tests:** normalizer/structurer bump → old spans superseded, extraction sees only fresh; claim citing a superseded row flagged; nothing hard-deleted; legacy rows (`is_active` default true) behave identically until first supersession; idempotent re-run with unchanged content supersedes nothing.
- **Acceptance:** after any re-ingest, active rows for a base ref = exactly the current chunking; orphan evidence cannot exist silently (it exists *visibly* as superseded).
- **Dependencies:** H2 (content hashes), H5 (element identity makes supersession matching robust; H6 can land against span identity if H5 slips, at reduced precision). **Risk:** medium (touches every evidence read; mitigated by the default). **Rollback:** flip reads back to ignoring `is_active` (one predicate), keep columns.

### H7 — Downstream provenance + loss reporting polish

- **Purpose:** close the small leaks (F7, F8, F9).
- **Migrations:** none.
- **Changes:** snapshot components bake `evidence_refs: [{source_type, source_ref, source_url, normalization_version}]` alongside `evidence_text` (`services/story_snapshot.py:80-89`; refs join the snapshot fingerprint deliberately); `_split_group` severance counting — pass-2 results whose `claim_index` falls outside their batch are counted + recorded in the extraction `validation_runs` detail; dropped drafts persist full draft JSON in `validation_runs` detail instead of `action_text[:80]` (`services/claim_extraction.py:395-404`).
- **Tests:** snapshot components name their sources without live rows; severed-pairing count appears when a group splits across an Action/Result boundary (fixture); a dropped draft is fully reconstructable from its `validation_runs` row.
- **Acceptance:** the frozen snapshot is self-describing about provenance; every extraction-time loss is countable after the fact.
- **Dependencies:** none hard (any time after H1). **Risk:** low. **Rollback:** revert; snapshot fingerprint change forces one re-snapshot (harmless, versioned).

### H8 — End-to-end reconciliation + the V4 readiness gate

- **Purpose:** turn the invariants into executable checks; define "done" (this milestone is the gate in §7).
- **Changes:** `python -m app.tools.audit_pipeline` — a zero-LLM offline tool that walks the whole spine for a user and asserts every invariant in §6/§7, emitting a scorecard to `validation_runs` (kind `pipeline_audit`): source dispositions complete, element coverage 100%, ownership labeled, no active orphans, provenance walk (story component → claim → claim_evidence → evidence → element → document version → raw slice) closes for every approved story, normalization/structurer versions consistent. The full regression suite (§6) green is a precondition.
- **Tests:** the tool itself has fixture tests (a corrupted fixture DB per invariant must fail its check).
- **Acceptance:** the tool passes on the live u1 corpus after a full re-ingest. That pass **is** the V4 readiness gate.
- **Dependencies:** H1–H7. **Risk:** low. **Rollback:** n/a (read-only tool).

**Dependency graph:** P0 ∥ H1 ∥ H2 → H3 → H4 → H5 → H6 → H8; H7 any time after H1. Rationale for order: H1 first because it is the only *active* destruction of human decisions and is a one-day change; H2/H3 are foundations everything references; H4 lands the substrate unconsumed (low blast radius), H5 flips the consumer with a rollback lever; H6 needs H5's identity to supersede precisely; H8 last because it enforces everything.

---

## 4. Phase 3 — Pipeline stage audit (verified) & Phase 4 — irreversible transformations

### 4.1 Updated pipeline diagram (current, verified)

```
 Drive (MCP server extracts PDF/Docs→flat   GitHub (README.md + commit    Uploads (.txt/.md)
 text server-side; no parser in repo)       messages only, via MCP)        uploads.py:61
        │                                        │                             │
        ▼                                        ▼                             ▼
 policy filter — rejected candidates dropped, unrecorded         source_policy.py:43,79,94
        │
        ▼
 gather_source_documents — NOTHING PERSISTED; Drive/GitHub read errors warned+skipped,
   uploads errors fatal (asymmetric)                        services/roster.py:118-190
        │
        ▼
 normalize_source_text — IRREVERSIBLE IN PLACE (input discarded)   text_normalization.py:61
        │
        ▼
 SourceDocument{type, ref, title, text} — mime/mtime/size dropped   domain/roster.py:42-49
        │
        ├─► detection (heuristic 1-per-file | LLM, docs truncated @4000) → HUMAN confirms
        ▼
 run_roster_assignment                                       services/roster.py:334-401
   chunk_normalized_text — paragraph chunks, exact spans, NO heading/order/adjacency
   commits → repo-ref · README → FORCE-assigned · else → context-free per-chunk guess
   assign_evidence UNCONDITIONAL — human corrections clobbered        :369, :389
        │
        ▼
 evidence(user, type, ref#chars=a-b → chunk_text, experience_id, norm_version)
   span change ⇒ new rows, old rows live forever (no delete_evidence, no tombstone)
        │
        ▼
 extraction (per confirmed entity; batches ≤30/14K — cross-batch Results silently
   missing) → PAR validation (structural drops keep action[:80]) → claims
        │
        ▼
 problem spaces (recorded partitions) → story synthesis (gated, quarantined) →
 HUMAN review (full provenance: quotes + URLs — richest point)   api/stories.py:95-110
        │
        ▼
 snapshot FREEZE — evidence refs/URLs/version dropped, chunks "\n"-joined
        │                                            services/story_snapshot.py:80-89
        ▼
 render (bullets → strings) · pipeline/tailoring/outreach (number gates fail closed)
 dashboard (/master-cv/latest: claim adapter → story snapshots = 0 claims)  [P0 bug]
```

### 4.2 Irreversible transformations — the Phase 4 questions

| Transformation | Why irreversible | Should it remain? | Delayable? | Made reversible? | Provenance eliminates the loss? |
|---|---|---|---|---|---|
| MCP/extraction flattening | Original bytes never fetched; server returns flat text | **No** as sole record | No (extraction must happen) | **Yes** — capture raw payload (H2); V4 parsers capture structure at parse time (H4 schema) | Yes: raw + elements make the flatten a derived view |
| Normalization | Input discarded in the same call; reflow heuristics not invertible | Transform yes, in-place destruction **no** | Yes — becomes a derived view of stored raw | **Yes** (H2 + H3: recomputable, version-enforced) | Yes |
| Chunking | Boundaries into unstored text; structure not carried | Chunking yes, structure-blindness **no** | n/a | **Yes** — element-derived chunks, order/section persisted (H4/H5) | Yes |
| Entity assignment | Overwrites prior value; no record of method or history | Assignment yes, unlabeled overwrite **no** | Ownership decision moves earlier (section-level, from structure) | **Yes** — method labels + human pins (H1), section inheritance (H5) | Yes |
| Evidence supersession | Today: orphaning (worse than irreversible — stale stays live) | Replacement yes, orphaning **no** | n/a | **Yes** — tombstones + reconciliation (H6) | Yes |
| Structural claim drops | Draft content truncated to 80 chars | Drop yes, truncation **no** | n/a | **Yes** — full draft in `validation_runs` (H7) | Yes |
| Batch-severed Results | Honest `missing`, but severance uncounted | Policy yes, silence **no** | Partially (element-ordered batching reduces severance) | Countable, not reversible (H7) | Partially — counted loss is auditable loss |
| Snapshot freeze | Refs dropped; only text + int ids survive | Freeze **yes** (by design) | n/a | Refs baked in (H7) — the freeze stays, the provenance loss goes | Yes |
| Human review decisions | By design | **Yes** | No | No — and must not be | Already carries provenance (attestations, notes) |
| Outbound send | Bytes leave the machine | **Yes** | Already latest possible | No | Gated (state machine + number gates) — correct as-is |

After H1–H8, the only irreversible transformations remaining are the three the architecture *intends*: canonical capture (which loses nothing), human decisions, and outbound sends.

---

## 5. Phase 5 — Final recommended architecture

```
 acquire (Drive/GitHub/uploads; V4: PDF/DOCX/Obsidian parsers)
        │
        ▼
 ① CANONICAL CAPTURE (immutable)                    source_documents + versions (H2)
    raw payload · content_hash · extractor identity · metadata · disposition report
        │
        ▼
 ② CANONICAL STRUCTURE (derived for text, parsed for PDF/DOCX)   source_elements (H4)
    hierarchy · order · type · verbatim raw spans · full-coverage reconciliation
        │
        ▼
 ③ derived views, versioned + recomputable                                (H3)
    normalized_text = f(raw, NORMALIZATION_VERSION) · tree = f(raw, STRUCTURER_VERSION)
        │
        ▼
 ④ ownership as data                                                       (H1+H5)
    section-scoped assignment · inherited by chunks · method-labeled ·
    human pins immutable · truncations logged
        │
        ▼
 ⑤ evidence with lifecycle                                                 (H6)
    element-derived chunks · document order as columns · supersede/tombstone ·
    reconciliation per re-ingest · reviewed-on-stale surfaced
        │
        ▼
 ⑥ semantic interpretation — UNCHANGED (already sound)
    extraction → PAR validation → problem spaces → stories → human review
        │
        ▼
 ⑦ freeze with provenance                                                  (H7)
    snapshot carries evidence refs · render/tailoring/outreach unchanged
```

**Constraint check** (every architectural constraint from the brief, mapped): raw source immutable → ①; claims never overwrite source → claims live in separate tables citing evidence (already true) + ① is append-only; ownership survives ingestion → ④; human review not silently overwritten → H1 pins + existing claim/story protections; normalization versioned → ③ enforced by H3; evidence lifecycle explicit → ⑤; orphan evidence impossible silently → ⑤; all documents reconcile → ② coverage + H8 tool; every claim traces to source → provenance walk closes at H8; every element has explicit disposition → ② `extraction_status`; silently dropped = 0 → H2 report + ② coverage + H8.

**Future-source support without redesign:** Markdown/text (structurer, day one) · Git repos (READMEs/commits already flow; richer file capture = more `source_document` rows) · PDF/DOCX (M23 parser populates `source_elements` directly — the only new code is the parser) · Obsidian (vault notes are Markdown documents with `vault` source_type; frontmatter/wikilinks become element types; the 74-LOC `app/second_brain/` stub is superseded and should be deleted in M23).

---

## 6. Required regression suite (pre-V4)

Grouped by area; every test names its pass criterion. Suites marked **[V4-gated]** define the contract now and activate when the M23 parser exists; everything else runs offline, zero credentials, before the gate.

**Ingestion & capture (H2)**
1. Raw fidelity: gathered raw_text byte-identical to client payload — *pass: equality*.
2. Idempotent capture: same content twice → one version row — *pass: row count stable*.
3. Content change: new version active, old present+inactive — *pass: both rows, one active*.
4. Disposition completeness: N discovered = ok + read_failed + policy_excluded — *pass: sum equality, every entry has a reason*.
5. Symmetric failure: one failing source (each of Drive/GitHub/uploads) never aborts the gather — *pass: report entry, remaining sources ingested*.

**Normalization & versioning (H3)**
6. Golden digest: corpus output hash matches committed digest for `NORMALIZATION_VERSION` — *pass: digest equality; any rule edit without bump fails*.
7. Recomputability: `normalize(raw)` reproduces the text every active span points into — *pass: slice equality per evidence row*.
8. Characterization: hyphen-join and soft-blank edge cases produce documented output — *pass: exact expected strings*.

**Structure (H4)** — Markdown ingestion, plus **[V4-gated]** PDF/DOCX
9. Golden trees: fixture Markdown (nested headings, lists, code, tables-as-text) → exact expected element tree — *pass: tree equality incl. types, levels, order*.
10. Coverage invariant: element spans + separators cover raw exactly, property-tested over the corpus — *pass: zero uncovered chars, zero overlaps*.
11. Structurer digest keyed by `STRUCTURER_VERSION` — *pass: as #6*.
12. **[V4-gated]** PDF/DOCX: parser populates the same schema; reconciliation counters (pages expected/processed, elements detected/persisted/unsupported/failed) balance; `silently_dropped_elements = 0` — *pass: counter equalities; a deliberately-corrupted fixture yields `parser_error` dispositions, never absence*.

**Chunking (H5)**
13. Chunks never cross element boundaries — *pass: every chunk maps to one element*.
14. Oversized element splits keep uniform ownership + contiguous sequence — *pass: one owner, ordered indices*.
15. Order round-trip: persisted `(version, sequence_index)` reconstructs document order — *pass: equality with source order*.

**Ownership & assignment (H1+H5)**
16. **Cooper regression:** multi-section fixture; a Result paragraph with zero entity tokens under a "Cooper.ai" heading — *pass: assigned to Cooper via section inheritance; fails on `None` or any other entity*.
17. Human pin survival: manual chunk assign + manual section assign, then full re-run — *pass: both unchanged, methods still `human`*.
18. Method labeling: every assignment path stamps its label — *pass: no NULL methods on new rows*.
19. Structureless fallback: flat text with no headings still refuses lexical ties — *pass: tie → unassigned queue*.
20. Truncation visibility: an over-budget doc/chunk in an LLM prompt increments the truncation counter — *pass: counter > 0 recorded*.

**Re-ingestion & lifecycle (H6)**
21. Span/boundary shift: old rows superseded (inactive, successor-linked), extraction reads only fresh — *pass: active set == fresh chunk set*.
22. Reviewed-on-stale: an approved claim citing a superseded row appears in the reconciliation report — *pass: surfaced, not auto-resolved, nothing deleted*.
23. No-op re-ingest: unchanged content supersedes nothing — *pass: zero lifecycle writes*.

**Claim provenance (existing + H7)**
24. Walk closure: for every claim, claim → claim_evidence → evidence → element → version → raw slice; quotes locate in the slice — *pass: every hop resolves*.
25. Dropped-draft retention: a structurally-dropped draft is fully reconstructable from `validation_runs` — *pass: full P/A/T/R JSON present*.
26. Batch severance: a group split across an Action/Result boundary records the severed count — *pass: count == fixture expectation*.

**Snapshots & resume generation (H7 + existing)**
27. Snapshot self-provenance: components carry `evidence_refs`; resolvable to URLs without live claim rows — *pass: refs present + well-formed after deleting live rows in the test DB*.
28. Existing gates re-asserted: resume-ready render gate, per-bullet number gate, cross-story `DuplicateMetricError` (already covered — `tests/test_full_v3_loop.py`, `tests/test_story_snapshot.py`; keep green).

**Dashboard & human review (P0 + existing)**
29. Story-shaped snapshot serializes with components (P0) — *pass: claim list non-empty, refs correct*.
30. Existing protections re-asserted: reviewed claims never re-queued; decided stories never replaced; story answers survive re-synthesis (already covered; keep green).

**End-to-end reconciliation (H8)**
31. `audit_pipeline` on a seeded fixture corpus: all invariants pass — *pass: scorecard all-green*.
32. Negative controls: one corrupted fixture DB per invariant (orphan evidence, missing disposition, coverage gap, unlabeled assignment, broken walk) — *pass: the tool fails the specific check, names the offending rows*.

---

## 7. V4 readiness gate

V4 development (M23+) may begin when, and only when, all of the following hold on the **live u1 corpus** after a full re-ingest through the hardened pipeline:

1. **Suite:** the full regression suite (§6, minus V4-gated items) is green offline with zero credentials, alongside the existing 761+ tests.
2. **Capture:** every source that feeds any active evidence row has an active `source_document_version` with raw text; gather report shows `silently_dropped_sources = 0`.
3. **Coverage:** every active document version's element tree covers its raw text exactly; every element has a disposition.
4. **Ownership:** every active evidence row carries an `assignment_method`; all pre-existing human corrections re-applied as `human` pins; `audit_pipeline` confirms zero machine overwrites of pins under a forced re-run.
5. **Lifecycle:** zero active orphans (every active row corresponds to current chunking); any reviewed-claim-on-superseded-evidence cases surfaced and human-resolved.
6. **Provenance:** the walk (approved story component → … → raw slice) closes for 100% of approved stories; `eval_stories` invariants remain zero (invented metrics, orphan components, duplicates, cross-space contamination).
7. **Versioning:** `NORMALIZATION_VERSION` and `STRUCTURER_VERSION` digest tests in place; all active rows stamped with current versions.
8. **The Cooper test:** regression #16 passes, and the live Cooper.ai entity's evidence (including the historically fragile chunk-129 reassignment) is section-pinned or section-inherited — no lexical guess in its provenance.

Sign-off artifact: the `audit_pipeline` scorecard row in `validation_runs`, plus this document updated with the gate-passing run's date.

---

## Appendix — corrections to the earlier audit

- `SourceDocument` **does** carry `title` (`domain/roster.py:48`); the loss point is evidence persistence, not gathering (F11).
- The earlier H4 ("heading context in the assigner prompt") is replaced: prompt context mitigates but does not fix — ownership must be *persisted at the section level from a persisted tree* (H4/H5 here), or re-ingestion re-guesses it forever.
- The earlier plan had no enforcement milestone; H8 and the readiness gate close that.
- Everything else in `PIPELINE_LOSS_AUDIT.md` §§1–6 was verified accurate and is incorporated by reference.
