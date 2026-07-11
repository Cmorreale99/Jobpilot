# Pre-V4 Pipeline Hardening — Information-Loss Audit

> **Superseded in part:** the milestone plan in §7 (H1–H6) is replaced by the verified
> roadmap in `PIPELINE_HARDENING_PLAN.md` (P0 + H1–H8, source-elements design, regression
> suite, V4 readiness gate). The findings in §§1–6 were re-verified there and stand.

**Date:** 2026-07-11
**Scope:** the real, shipped source→evidence→claim→story→render pipeline, traced from code (not from the intended architecture). Every claim below carries a `file:line` reference, verified against the working tree at commit `313dd8f`.
**Mandate:** find every irreversible transformation before semantic interpretation, classify it, and produce the smallest sequence of architectural changes that eliminates every unjustified one before V4 begins.

---

## 0. The headline finding

The Cooper failure diagnosis in the project brief is correct, and it is worse than stated:

> The source bytes survived. The relationship between those bytes and their owning project was destroyed.

In the current implementation **the source bytes do not survive either.** The only durable copy of any source text in the entire system is `evidence.chunk_text` — a **post-normalization, post-chunking** fragment, written only for chunks that reached roster assignment. Specifically:

1. **No raw capture exists.** `cv_sources` (with its `raw_text` column, `app/db/models.py:62-79`) has **zero writers and zero readers** — it is dead schema from V1. `app/services/master_cv_ingestion.py` referenced in CLAUDE.md does not exist. The real acquisition path is `gather_source_documents` (`app/services/roster.py:118-190`), which fetches, normalizes, and returns **in-memory** `SourceDocument`s. The pre-normalization text is discarded when the function returns.
2. **There is no PDF/DOCX parser in the repo.** Real Drive extraction is delegated entirely to the MCP server (`app/integrations/mcp/drive.py:252-260`, interface note `app/integrations/base.py:82-83`); `python-docx` is present only as a render-side transitive dep of `docxtpl`. Whatever structure the file had is flattened to one string before JobPilot ever sees it, and the original bytes are never fetched or stored.
3. **Ownership is inference, persisted as fact.** "This chunk belongs to project X" is a single nullable FK (`evidence.experience_id`, `models.py:243`) written by a per-chunk, context-free, order-blind assigner that never sees headings, neighbors, or document order — because Steps 1–3 already destroyed them. Every downstream stage then treats that guess as ground truth.

So the architectural principle in the brief — *structure is source data* — is currently violated twice before the first LLM call: once at extraction-flattening, once at normalization+chunking. Assignment then has to *reconstruct* what acquisition destroyed. That is the Cooper failure's root cause, in code.

---

## 1. Pipeline diagram (actual implementation)

```
                          ┌────────────────────────────────────────────────────┐
                          │ NOTHING IS PERSISTED UNTIL ROSTER ASSIGNMENT (¶)   │
                          └────────────────────────────────────────────────────┘

 Drive (MCP server extracts     GitHub (README.md only +      Local uploads
 PDF/Docs→flat text server-side) commit messages via MCP)     (.txt/.md only)
   mcp/drive.py:252              mcp/github.py:250,262          uploads.py:61
        │                             │                             │
        ▼                             ▼                             ▼
 policy filter (silent drop of rejected candidates)   source_policy.py:43,79,94
        │
        ▼
 gather_source_documents ──────── per-source read errors caught + warned →
   services/roster.py:118-190     SOURCE SILENTLY SKIPPED (Drive/GitHub only;
        │                         uploads errors are fatal — asymmetric)
        ▼
 normalize_source_text  (IRREVERSIBLE IN PLACE — raw text discarded here)
   domain/text_normalization.py:61-87   NORMALIZATION_VERSION=1, manual bump
        │
        ▼
 SourceDocument{source_type, source_ref, title, text}   ← mime/modified_time/
   domain/roster.py:42-49                                  size/language DROPPED
        │
        ├──► run_roster_detection (heuristic 1-entity-per-file, or LLM authoring
        │      entities from docs TRUNCATED at 4000 chars)  services/roster.py:193
        │           │
        │           ▼
        │    HUMAN confirms/renames/merges/discards roster   api/roster.py
        │
        ▼
 run_roster_assignment   services/roster.py:334-401
   chunk_normalized_text (paragraph chunks ≤1200 chars, exact spans into
   NORMALIZED text; no heading/adjacency/order persisted)  domain/chunking.py:74
        │
        ├─ commits → whole message, assigned by repo-ref alias      :361-372
        ├─ README  → every chunk FORCE-assigned to repo entity      :375-377
        └─ Drive/uploads → per-chunk assigner (lexical overlap, or LLM
             seeing 800-char truncated chunk strings, NO context)   :378-379
        │
        ▼
 (¶) evidence rows: (user_id, source_type, source_ref#chars=a-b) → chunk_text,
     experience_id (THE OWNERSHIP GUESS), normalization_version
     · re-run CLOBBERS manual human assignments        services/roster.py:369,389
     · span change ORPHANS old rows (no delete_evidence exists anywhere)
        │
        ▼
 claim extraction (per confirmed entity, its chunks only; two-pass; batches
   ≤30 chunks/14K chars — Action↔Result pairs severed across batches become
   result=missing, unlogged per-pairing)       services/claim_extraction.py,
        │                                       llm/extraction.py:64-82
        ▼
 PAR validation (pure): STRUCTURAL → claim DROPPED (only action_text[:80]
   survives in the report), ABSENCE → recomputable, INTEGRITY → flagged
        │                               domain/par_validation.py
        ▼
 claims + claim_evidence (outcome_quote located by SUBSTRING match, no offsets)
        │
        ▼
 problem-space detection (recorded LLM partitions, first-write-wins) →
 story synthesis (selection, gated, quarantined; decisions retained) →
 HUMAN story review (richest provenance: quotes + click-through URLs — the
   ONLY place full provenance is visible)         api/stories.py:95-110
        │
        ▼
 story snapshot (FREEZE): evidence collapses to "\n"-joined evidence_text;
   source_type/source_ref/source_url/normalization_version DROPPED
        │                               services/story_snapshot.py:80-89
        ▼
 render (bullets → bare strings) · pipeline/tailoring/outreach (number gates,
   fail-closed) · dashboard (/master-cv/latest uses the CLAIM adapter → story
   snapshots serialize as 0 claims — latent bug, api/dashboard.py:31,45)
```

---

## 2. Per-stage analysis and classification

Classification key: **Lossless** (nothing destroyed) / **Derived** (adds, never replaces) / **Lossy** (destroys; must be justified) / **Irreversible** (prior state unreconstructable).

| # | Stage | Code | Class | What is destroyed | Justified? |
|---|-------|------|-------|-------------------|-----------|
| 1 | Discovery + policy filter | `source_policy.py:43,79,94` | Lossy (filter) | Rejected candidates dropped silently, unlogged | Partially — filtering is intended; the silence is not |
| 2 | Body read / extraction | `mcp/drive.py:252-260,406-417`; `mcp/github.py:250-266` | **Irreversible** | All file structure (headings, lists, tables, pages, styles); original bytes never fetched; GitHub: everything but README.md + commit subject text; commit diffs/file lists | **No.** Nothing before this point is retained anywhere |
| 3 | Gather (per-source error handling) | `services/roster.py:138-140,161-162,173-174` | Lossy | A failing Drive/GitHub source is skipped with only a log warning; uploads fail loud (asymmetric) | No — silent per-source loss |
| 4 | Normalization | `text_normalization.py:61-87` | **Irreversible** | Original line/whitespace layout; heuristic soft-blank reflow can merge real paragraphs; hyphen-join can corrupt (`state-\nof-the-art`); pre-norm text discarded | **No.** The transform is fine; discarding its input is not |
| 5 | `SourceDocument` boundary | `domain/roster.py:42-49` | Lossy | mime, modified_time, folder, size, language, commit authored_at — no field exists for them | No — cheap to keep |
| 6 | Roster detection | `domain/roster.py:100-139`; `llm/roster.py:159` | Derived | Nothing (proposals only; human confirms; decision-preserving) — but LLM sees docs truncated at 4000 chars (`llm/roster.py:37`) | Yes (with the truncation caveat) |
| 7 | Chunking | `chunking.py:74-82` | Derived w.r.t. normalized text, **Lossy w.r.t. structure** | Section/heading membership, adjacency, explicit order (spans allow re-sorting only within one normalization_version; `list_assigned_evidence` returns id-order, `claim_repository.py:320`); oversized-paragraph splits can sever Problem from Result | **No** — this is the Cooper crux |
| 8 | Chunk→entity assignment | `services/roster.py:334-401`; `domain/roster.py:142`; `llm/roster.py:186` | **Inference persisted as fact** | Context (assigner sees bare strings); README chunks force-assigned with zero content check; a wrong-but-confident assignment is fully silent | **No** |
| 9 | Assignment re-run semantics | `services/roster.py:369,389` | **Irreversible loss of human decisions** | Manual `POST /roster/evidence/{id}/assign` corrections silently clobbered by the next `/roster/assign`; no pinned flag; untested | **No — violates the "human decisions preserve provenance" invariant** |
| 10 | Evidence lifecycle | no `delete_evidence` exists (verified repo-wide) | Lossy (by accumulation) | Span/normalizer changes insert new rows and orphan old ones, which keep stale text+assignment and **still feed extraction**; mixed normalization_versions make spans incomparable | No |
| 11 | Claim extraction | `services/claim_extraction.py`; `llm/extraction.py` | Derived + gated | Structural drops keep only `action_text[:80]` + violation strings (`claim_extraction.py:395-404`); batch splits sever Action↔Result silently per-pairing (`extraction.py:64-82`); grounding drops logged | Mostly — losses are recoverable via re-extraction; the reporting is too thin |
| 12 | PAR validation | `par_validation.py` | Derived (pure) | Nothing directly; "verbatim" = whitespace-collapsed substring, not offsets (`:96-103`) | Yes |
| 13 | Problem spaces + synthesis | `domain/problem_space.py`; `services/story_synthesis.py` | Derived, gated | Quarantined drafts discarded (violations recorded); stale-space machine drafts hard-deleted (decided/answered guarded, `story_synthesis.py:274-291`) | Yes — decisions retained everywhere |
| 14 | Human review (claims/stories) | `api/stories.py` | **Point of no return #2 (by design)** | — | Yes — retained with provenance |
| 15 | Story snapshot | `services/story_snapshot.py:80-89`; `domain/story_snapshot.py:126-138` | Lossy projection | Per-chunk `source_type/source_ref/source_url`, chunk separation (`"\n"`-join), `normalization_version` all dropped; only int `claim_ids` survive, resolvable only while live rows exist | Partially — freezing is right; dropping refs is not |
| 16 | Render / tailoring / outreach | `resume_context.py:154-181`; `tailoring.py`; `llm/drafting.py` | Lossy by design (a view) | Bullets → bare strings; number gates fail closed; outbound send is **point of no return #3**, correctly gated | Yes |
| 17 | Dashboard | `api/dashboard.py:31,45` | Bug | `/master-cv/latest` uses the claim-shaped adapter, so a story snapshot serializes to 0 claims (`master_cv_from_any_snapshot` exists and is unused here) | No — latent bug |

**Well-built parts (keep, don't touch):** the claim→story→approval→render segment is genuinely loss-safe. All LLM failures are loud typed errors with no silent heuristic substitution; human decisions (approve/reject/exclude/attest, recorded partitions, discarded/merged entities) survive every re-run; drops are gated and logged; the number gates fail closed; the frozen renderer is a pure view. The audit's problems are concentrated **upstream of extraction** and at the **snapshot's provenance drop**.

---

## 3. Lossy transformations, ranked by severity

| Rank | Loss | Where | Why it matters |
|------|------|-------|----------------|
| **L1** | No canonical raw capture: pre-normalization text and original bytes persisted nowhere; `cv_sources` dead | `services/roster.py:118-190`; `models.py:62-79` | Point-of-no-return #1 (canonical source persistence) **does not exist**. Every other fix builds on this |
| **L2** | Human assignment corrections silently clobbered on re-run | `services/roster.py:369,389` | The only irreversibility allowed for human decisions is *retention*, not destruction. Active, silent, untested |
| **L3** | Structure (heading ownership, adjacency, order) destroyed before assignment; assignment re-infers it per-chunk, context-free; README force-assignment unchecked | `chunking.py`; `domain/roster.py:142`; `services/roster.py:375-379` | The Cooper failure's mechanism. A wrong assignment is silent and poisons every downstream stage |
| **L4** | Normalization irreversible in place; `NORMALIZATION_VERSION` bump is unenforced manual discipline | `text_normalization.py:33-38` | A forgotten bump silently dangles every stored span with zero detection |
| **L5** | Orphaned evidence: span/normalizer change inserts new rows, old rows never deleted and still feed extraction | no `delete_evidence` anywhere | Duplicate/contradictory evidence accumulates invisibly; mixed-version spans are incomparable |
| **L6** | Per-source read failures swallowed (Drive/GitHub) — source skipped with a warning | `services/roster.py:138-174` | "The document didn't load" becomes indistinguishable from "the document has no evidence" — the exact Paper-recommender confusion |
| **L7** | Snapshot drops evidence refs (source_ref/URL/version); `evidence_text` is a `"\n"`-joined blob; downstream only has int `claim_ids` | `services/story_snapshot.py:80-89` | The frozen version — the thing consumed by matching/tailoring/outreach — cannot prove where its text came from |
| **L8** | Batch splits sever Action↔Result pairs → Result honestly `missing`, but the severance is unlogged per-pairing | `llm/extraction.py:64-82` | Real results silently demoted to missing; human pays the re-attestation cost |
| **L9** | Structural claim drops keep only `action_text[:80]` | `claim_extraction.py:395-404` | The drop is loud, but the dropped content isn't auditable |
| **L10** | Quote location by substring match, not offsets; spans point into unstored normalizer output | `par_validation.py:96-103` | Ambiguous provenance when a quote repeats; unverifiable once raw text is gone (fixed for free by L1) |
| **L11** | Source metadata (mime, title→evidence, modified_time, size) dropped at the `SourceDocument` boundary | `domain/roster.py:42-49` | Cheap context permanently unavailable to assignment and audit |
| **L12** | LLM prompt truncations: roster detection 4000 chars/doc, assignment 800 chars/chunk | `llm/roster.py:37-38` | Entities/disambiguators beyond the truncation are invisible — silent |
| **L13** | Dashboard serializes story snapshots as 0 claims (wrong adapter) | `api/dashboard.py:31,45` | Latent bug, trivial fix |
| **L14** | Policy-rejected candidates dropped without a record | `source_policy.py` | Low, but "what was excluded and why" should be a report, not silence |

---

## 4. Points of no return — intended vs. actual

| # | Intended | Actual |
|---|----------|--------|
| 1 | Canonical source persistence, zero loss | **Missing entirely.** First durable write is post-normalization, post-chunking, post-assignment `evidence.chunk_text` |
| 2 | Human review decisions, irreversible with provenance | Claims/stories: **correct** (retained everywhere). Roster evidence assignment: **violated** — manual corrections clobbered (L2) |
| 3 | Outbound communication | **Correct.** State-machine-gated, number-gated, fail-closed, never re-sent |

Everything between #1 and #2 should be reversible. Today, normalization (L4), chunk-identity (L5), and assignment history (L2) are not.

---

## 5. Architectural smells found

- **Flattening** — twice: extraction-side (MCP server / `read_text` → one string) and normalization reflow. `SourceDocument` has a flat `text` field; no structure representation ever exists in the system.
- **Premature normalization** — the raw text is destroyed *in the same function call* that normalizes it (`gather_source_documents`). Normalization should be a derived view of a stored original.
- **Implicit ownership** — `evidence.experience_id` is a bare FK carrying no record of *how* it was decided (heuristic? LLM? README force-assign? human?), so a human decision and a lexical guess are indistinguishable — which is exactly why re-runs clobber humans.
- **Context reconstruction** — the assigner re-infers, from bare strings, relationships (section membership) that the source stated explicitly. Heuristic linking downstream of structure destruction upstream.
- **State duplication without lifecycle** — re-ingestion inserts new evidence rows alongside stale ones; no supersession, tombstoning, or GC; both generations feed extraction.
- **Unenforced invariants by convention** — `NORMALIZATION_VERSION` ("BUMP THIS") and `GROUPING_PROMPT_VERSION` rely on developer memory; nothing in CI detects a rule change without a bump.
- **Dead schema masquerading as a guarantee** — `cv_sources.raw_text` looks like raw capture exists; it has never been written.
- **Asymmetric failure semantics** — uploads read errors are fatal, Drive/GitHub read errors are silent skips.
- **Provenance narrowing at the freeze** — the review card shows quote + URL; the snapshot that everything downstream consumes keeps neither.
- **Report-by-truncation** — dropped claims survive only as `action_text[:80]`.

Notably **absent** smells (credit where due): no silent LLM→heuristic substitution anywhere; no LLM authority over what survives (grounding gates everywhere); human decisions in the claim/story layer are never overwritten; idempotency is real in extraction/synthesis.

---

## 6. Redesigned pipeline (target shape)

The redesign keeps every downstream stage (extraction → validation → stories → review → snapshot → render) essentially as-is — they are already gated and decision-preserving — and rebuilds the front of the pipeline around one rule: **persist first, interpret later; every interpretation is a labeled, replaceable derivation of a stored original.**

```
 acquire bytes/extracted text  ──►  CANONICAL CAPTURE (new, point of no return #1)
                                    source_documents: content_hash, raw_text (as
                                    received), mime, title, modified_time, size,
                                    extractor identity, fetched_at
                                    + gather report: every skip/failure recorded
        │
        ▼
 normalized view (DERIVED)          normalize(raw) recomputable at any time;
                                    version bump ENFORCED by golden-corpus test
        │
        ▼
 structured chunks (DERIVED)        evidence gains sequence_index + section_ref
                                    (nearest governing heading) — structure
                                    travels WITH the chunk from here on
        │
        ▼
 assignment (LABELED INFERENCE)     evidence.assignment_method ∈ {heuristic, llm,
                                    readme_ref, human}; human is a decision:
                                    NEVER overwritten by a machine re-run;
                                    assigner receives section_ref + neighbors
        │
        ▼
 evidence lifecycle                 re-ingest supersedes (tombstone, is_active)
                                    instead of orphaning; extraction reads active
                                    rows only; reconciliation report per run
        │
        ▼
 extraction → claims → stories → review   (unchanged, already sound)
        │
        ▼
 snapshot carries per-component evidence REFS (type/ref/url/version), not just
 flattened text  →  render / pipeline / outbound (unchanged)
```

This is deliberately the **substrate the V4/M23 exhaustive-ingestion spec assumes but does not define**: the spec (`docs/research/claude_jobpilot_exhaustive_ingestion.md`) specifies `SourceElement` capture for PDF/DOCX but never maps it onto the existing `evidence`/`span_ref`/`normalization_version` model, and proposes a parallel `ProjectEvidenceLink` table disconnected from the roster/claims layers. Milestones H1–H6 below define that mapping on the *current* text sources, so M23's PDF/DOCX parser plugs into an already-lossless spine instead of standing beside it.

---

## 7. Prioritized implementation plan

Smallest sequence that eliminates every unjustified loss. Ordered by (decision-loss urgency → foundations → structure → lifecycle → polish). Each milestone is independently shippable and offline-testable with zero credentials.

### H1 — Stop destroying human decisions (fixes L2, part of L3)

**Goal:** a human evidence assignment is a retained decision, symmetrical with claim/story review.
**Changes:**
- Migration `0016`: `evidence.assignment_method` (`heuristic|llm|readme_ref|human`, nullable for legacy) + `assigned_at`.
- `assign_evidence` gains a `method` arg; `api/roster.py:200` writes `human`; `run_roster_assignment` (`services/roster.py:369,389`) **skips any row whose method is `human`** and labels its own writes.
- README force-assignment recorded as `readme_ref` (it's currently indistinguishable from a scored assignment).
**Files:** `app/db/models.py`, `app/db/claim_repository.py`, `app/domain/claims.py` (in-memory repo), `app/services/roster.py`, `app/api/roster.py`, migration.
**Risk:** low (additive column, one guard clause).
**Tests:** manual assign → re-run `/roster/assign` → assignment unchanged (the currently-missing clobber test); method labels asserted per path; merge preserves method.
**Acceptance:** no machine write can change a `human`-method row; every evidence row created after H1 carries a method.

### H2 — Canonical source capture + loud gather (fixes L1, L6, L11, L14)

**Goal:** point of no return #1 exists: every gathered source's as-received text + metadata is durable *before* normalization; nothing is skipped silently.
**Changes:**
- Revive-or-replace `cv_sources` (prefer a fresh `source_documents` table + migration dropping the dead one): `(user_id, source_type, source_ref)` unique, `raw_text` (as received from client, **pre-normalization**), `content_hash`, `mime_type`, `title`, `modified_time`, `size_bytes`, `fetched_at`, `extractor` (e.g. `mcp:get_drive_file_content`, `local:read_text`).
- `gather_source_documents` writes/updates it (idempotent by content_hash) and returns a **GatherReport**: sources read, skipped (with reason), failed — persisted to `validation_runs` (kind `source_gather`). Uploads/Drive/GitHub error handling made symmetric: all recorded, none fatal-vs-silent by accident.
- `SourceDocument` gains the metadata fields (they now exist anyway).
**Files:** `app/db/models.py`, new store module, `app/services/roster.py`, `app/domain/roster.py`, migration, `app/api/roster.py` (surface the gather report).
**Risk:** low-medium (new table; gather signature grows a repo dependency — keep it optional-injected so pure callers stay pure).
**Tests:** raw text durable + pre-normalization byte-identical to client output; re-gather idempotent; a failing mock client lands in the report, not the void; report distinguishes `skipped_policy` / `read_failed` / `ok`.
**Acceptance:** for every evidence row's base ref, the pre-normalization text is recoverable from the DB; `silently_dropped_sources = 0` (all dispositions recorded).

### H3 — Enforce normalization versioning; make normalization a derivation (fixes L4, enables L10's fix)

**Goal:** a normalizer rule change without a version bump is a CI failure, and normalized text is always recomputable from stored raw.
**Changes:**
- Golden-corpus test: a fixture directory of pathological inputs; the test hashes `normalize_source_text` output over the corpus and compares against a committed digest keyed by `NORMALIZATION_VERSION`. Rule change + no bump → digest mismatch → red. Bump → developer re-blesses the digest, and the mismatch-handling path (H5) takes over migration.
- Store `normalization_version` on `source_documents` at gather time (which generation produced current downstream spans).
**Files:** `tests/test_normalization_versioning.py`, fixtures, small stamp in gather.
**Risk:** trivial.
**Acceptance:** editing any regex/rule in `text_normalization.py` without bumping the version fails the suite.

### H4 — Structure travels with the chunk (fixes L3 — the Cooper fix)

**Goal:** section ownership and document order are **source data carried on evidence**, not something assignment re-infers.
**Changes:**
- `chunk_normalized_text` returns chunks annotated with `sequence_index` and `section_ref` (span of the nearest preceding structural heading line — the normalizer already identifies structural lines; expose that instead of discarding it).
- Migration `0017`: `evidence.sequence_index`, `evidence.section_ref` (nullable text: the governing heading's text + span).
- Assigners receive context: `ChunkAssigner.assign` takes `(chunks_with_context, roster)` where each chunk carries its section heading + neighbor titles; the heuristic scores section-heading tokens **before** body tokens (a chunk under a "Cooper.ai" heading matches Cooper by structure, not vocabulary); the LLM prompt includes the heading line (cheap — one line per chunk, no budget blowup).
- README force-assignment keeps its label (H1) but now also records section context.
- `list_assigned_evidence` orders by `(base_ref, sequence_index)` instead of row id.
**Files:** `app/domain/chunking.py`, `app/domain/text_normalization.py` (export structural-line detection), `app/domain/roster.py`, `app/llm/roster.py`, `app/services/roster.py`, repos, migration.
**Risk:** medium — assigner interface change; heuristic behavior shifts (deliberately). Gate with a regression fixture reproducing the Cooper shape: a heading-owned block whose Result chunk contains no entity token must assign to the heading's entity, not `None`/wrong.
**Tests:** the Cooper regression fixture (currently missing — no test covers a boundary-split misassignment); heading-context beats lexical tie; order round-trips.
**Acceptance:** a chunk whose section heading names entity X can never be silently assigned to entity Y by vocabulary overlap; document order is a column, not a parse of `source_ref`.

### H5 — Evidence lifecycle: supersede, never orphan (fixes L5)

**Goal:** re-ingestion after source/normalizer change replaces evidence *visibly and reversibly* instead of accumulating live stale rows.
**Changes:**
- Migration `0018`: `evidence.is_active` (default true) + `superseded_by_id` (nullable self-FK).
- On assignment re-run: chunks of the current generation upsert as today; prior-generation rows for the same base ref whose `source_ref` no longer exists in the fresh chunk set are marked inactive (pointing at their nearest overlapping successor when determinable), **never deleted** — `claim_evidence` links stay intact and auditable.
- `list_assigned_evidence` / extraction grouping / overlap detection read active rows only; a reconciliation summary (rows active/superseded/new, mixed-version warnings) lands in `validation_runs` (kind `evidence_reconciliation`).
- A superseded row that backs a **reviewed** claim is reported loudly (the human decided on text that no longer exists upstream) — surfaced, never auto-resolved.
**Files:** repos (SQL + in-memory), `app/services/roster.py`, `app/services/claim_extraction.py` (read path), migration, `app/api/roster.py` (report surface).
**Risk:** medium — touches every evidence read; mitigated by defaulting `is_active=true` for all legacy rows (behavior identical until the first supersession).
**Tests:** normalizer bump → old spans superseded, extraction sees only fresh; claim citing a superseded row is flagged in the report; nothing hard-deleted.
**Acceptance:** after any re-ingest, `active rows for a base ref` = exactly the current chunking; zero orphaned-but-feeding rows.

### H6 — Downstream provenance + reporting polish (fixes L7, L8, L9, L13)

**Goal:** close the small, cheap leaks.
**Changes:**
- **Snapshot refs (L7):** `_prepare_renderable` (`services/story_snapshot.py:80-89`) additionally bakes `evidence_refs: [{source_type, source_ref, source_url, normalization_version}]` per component next to `evidence_text`. Snapshot fingerprint unchanged content → include refs in the fingerprint deliberately (a ref change is a real change).
- **Dashboard adapter (L13):** `api/dashboard.py:45` → `master_cv_from_any_snapshot`. One-line fix + test with a story-shaped snapshot.
- **Batch-severance logging (L8):** when `_split_group` splits, log + record (extraction `validation_runs` detail) the count of pass-2 results whose `claim_index` fell outside their batch — the severed pairings become countable instead of invisible.
- **Full drop retention (L9):** `dropped` report entries persist the full draft (problem/action/tools/result JSON) in the `validation_runs` detail instead of `action_text[:80]`.
**Files:** `app/services/story_snapshot.py`, `app/domain/story_snapshot.py`, `app/api/dashboard.py`, `app/llm/extraction.py`, `app/services/claim_extraction.py`.
**Risk:** low.
**Acceptance:** a snapshot component can name its sources without live rows; dashboard shows story snapshots correctly; every dropped draft is fully reconstructable from `validation_runs`.

### Sequencing and scope notes

- **Order:** H1 → H2 → H3 → H4 → H5 → H6. H1 first because it is the only *active destruction of human decisions* and is a one-day change. H2/H3 are the foundations everything else references. H4 depends on nothing but is riskier — land it after the foundations so the Cooper regression fixture can assert against stored raw text. H5 depends on H3's version stamping. H6 is independent and can interleave.
- **Explicitly out of scope (V4, not hardening):** the PDF/DOCX `SourceElement` parser (M23 builds it *on top of* H2's `source_documents` and H4's structure columns), pgvector retrieval, the disclosure gate (M24), the second-brain vault (`app/second_brain/` is a disconnected 74-LOC stub that follows neither design doc — leave it frozen or delete it as part of M23, not now).
- **What this plan deliberately does not touch:** extraction/validation/story/review/render logic. Those stages passed the audit — their failure modes are loud, their human decisions are retained, and their gates fail closed. Hardening ends where the pipeline is already honest.

---

## 8. Invariants to hold after hardening (the definition of done)

1. **Recoverability:** for every active evidence row, the pre-normalization source text is in the DB, and `normalize(raw)[start:end] == chunk_text` for its version.
2. **No silent source loss:** every discovered candidate ends in exactly one recorded disposition (ingested / policy-excluded / read-failed).
3. **Ownership is labeled:** every evidence row states how its assignment was made; `human` assignments are immutable to machine runs.
4. **Structure is data:** section ref + sequence index are columns; no stage re-infers document order or heading membership.
5. **Versioned derivations:** a normalizer change is impossible to ship un-bumped; mixed-version spans never co-feed extraction.
6. **Supersede, never orphan:** re-ingestion tombstones; nothing stale feeds extraction; reviewed claims on superseded evidence are surfaced.
7. **Provenance survives the freeze:** a snapshot component names its sources by ref and URL, not just by flattened text.
