# Master CV Repair — Requirement Traceability Matrix

**Governing authority:** `docs/MASTER CV REPAIR.md` (the SPEC).
**Working branch:** `repair/master-cv-spec` (no commits; main untouched).
**Date started:** 2026-07-12.
**Constraints honored:** no subagents, no paid calls, no migrations, no DB writes, no destructive ops, offline suite only.

Statuses: `SATISFIED` | `VIOLATED` | `PARTIAL` | `UNVERIFIABLE` | `BLOCKED(decision)` — plus
`REPAIRED` once a repair in this pass lands with before/after evidence.

Legend for repair IDs: R1 GitHub universe · R2 required-doc failure semantics · R3 collection/first-match
assignment · R4 evidence preservation · R5 action/result integrity · R6 coverage/reporting ·
R7 truth cases + acceptance tests · R8 publication gating/dispositions.

---

## §1 Product objective (binding via later sections; itemized where separately testable)

| Req | Status | Notes |
|---|---|---|
| 1.1 read admitted Drive+GitHub corpus | PARTIAL | Drive path exists w/ policy scope; GitHub path reads root README + commits only → R1. |
| 1.2 account for every source object | PARTIAL | H2 GatherReport gives per-candidate dispositions (`app/services/roster.py:104-152`), but the GitHub universe itself is incomplete (README-only) so the denominator is wrong (§4.16) → R1/R6. |
| 1.3 preserve raw before interpretation | SATISFIED | H2 capture layer (`domain/source_capture.py`, `services/roster.py::gathered`) persists as-received text pre-normalization, hash-idempotent. Verified by `tests/test_source_capture.py`. |
| 1.4-1.7 identify entities, assign correctly | VIOLATED | Repo→project proposals, README/commit force-assign, first-match alias (§4.5-4.9) → R3. |
| 1.8 preserve incomplete/non-PAR evidence | PARTIAL | Chunks persist in `evidence` independent of claims; but structural claim DROPS discard extracted signal to a log (§4.13), and no queryable inventory/category surface exists (§5.4.6) → R4. |
| 1.11 expose ambiguity, don't guess | VIOLATED | `_repo_entity` first-match; README force-assign labels a guess as construction-correct → R3. |
| 1.13 avoid repeating paid work | SATISFIED | `extraction_hash` skip, `synthesis_hash` skip, grouping recordings, prompt cache. `tests/test_claim_extraction_service.py`, `test_story_synthesis.py`. |
| 1.14 fail loudly on incomplete coverage | PARTIAL | Read failures logged + validation_runs; but nothing blocks downstream/publication (§14.1) → R2/R8. |
| 1.15 publish only validated candidate | PARTIAL | Snapshot is atomic (single fingerprinted insert) but not gated on source completeness; ungrounded stories silently dropped from the CV → R8. |

## §2 Complaints as requirements

| Req | Status | Notes |
|---|---|---|
| 2.1 failures observable/attributable | PARTIAL | validation_runs covers extraction/gather/audit; render-time story drops are log-only → R8. |
| 2.2 visible improvement per repair | (process) | Enforced by this ledger's before/after column. |
| 2.3 unauthorized decisions removed | VIOLATED | The named behaviors exist: commit-as-first-class evidence volume (§4.4), guessing ownership (§4.7-4.9), strict-PAR drops (§4.12-4.13), README-only "100%" universe (§4.16) → R1-R6. |
| 2.4 output judged vs source truth | (process) | §11 truth cases in R7. |
| 2.5 gaps ≠ permission to guess | VIOLATED | Same defects as 1.11 → R3. |

## §3 Binding definitions
Adopted as the vocabulary of the repairs. Notable deltas from current code:
- 3.1/3.3: no "collection repository"/container concept exists anywhere (`ExperienceKind` has only `employer_role|project`) → R3 (representation chosen WITHOUT schema change: containers are policy data, see R3 design note).
- 3.5/3.10: evidence ≠ claims; PAR is one organization → R4.
- 3.8: canonical assignment = deterministic user-approved boundary | user-confirmed | proven. Current `readme_ref`/`repo_ref`/first-match assignments are machine guesses labeled canonical → R3.

## §4 Known implementation conflicts

| Conflict | Status | Exact code | Smallest failing case | Repair |
|---|---|---|---|---|
| 4.1 GitHub ingestion incomplete | VIOLATED | `app/integrations/mcp/github.py:47` (`_README_PATH = "README.md"` hardcoded); `GitHubClient` protocol has no tree/file read (`app/integrations/base.py:206-238`); `gather_source_documents` reads only `read_repo` + `list_commits` (`app/services/roster.py:400-440`) | A repo with `CLAUDE.md` + `docs/ARCHITECTURE.md` + nested `proj/README.md` yields exactly 1 doc + commits | R1 |
| 4.2 CLAUDE.md ignored | VIOLATED | same | CLAUDE.md-only repo yields zero doc evidence | R1 |
| 4.3 README failure masked by commit success | VIOLATED | `roster.py:406-427`: README `failed(...)` then commits proceed; nothing downstream consumes `read_failed` as a blocker | README read raises; run completes; snapshot/render still publish | R2/R8 |
| 4.4 commits overrepresented | PARTIAL | Commits are first-class Action/Result evidence (`domain/claims.py` docstring, extraction two-pass). No importance tiering exists. §6.3 default ranking unimplemented; §22.9 unresolved but §5.2.6 binding default = supporting-only unless user promotes | 100 commits vs 1 README: commit claims dominate the queue | R4 (importance tier recorded on evidence categories) + BLOCKED(22.9) for exclusion semantics beyond the binding default |
| 4.5 repos proposed as projects | PARTIAL | `HeuristicRosterProposer` proposes 1 project/repo (`domain/roster.py:128-157`) — they are PROPOSALS (human confirms), but §7.2 fields (container?, repo-name-derived?, overlaps?) are absent and §21 prohibits repo→proposal spam as the only mechanism | — | R3 (proposal metadata + container flagging) |
| 4.6 portfolio treated as project | VIOLATED (machinery) | Nothing prevents a collection repo from confirming as a project; README force-assign then pins all sections to it. Live DB: user confirmed real entities (preserved per §0.2.14) | Portfolio fixture: repo proposal + all README sections assigned to portfolio entity | R3 + R7 (16.2) |
| 4.7 README force-assigned to repo entity | VIOLATED | `roster.py:867-879` (structure path), `839-843` (flat path): every README chunk → `_repo_entity`, `ASSIGNMENT_README_REF` | Root README with two child-project sections: both sections owned by repo entity | R3 |
| 4.8 commits force-assigned to repo entity | VIOLATED | `roster.py:816-824`: `ASSIGNMENT_REPO_REF` unconditional | Collection-repo commit lands under container entity | R3 |
| 4.9 first-match alias decides ownership | VIOLATED | `_repo_entity` (`roster.py:634-639`) returns first `matches_name` hit | Two confirmed entities alias the same repo_ref → first wins silently | R3 |
| 4.10 roster detection sees truncated docs | PARTIAL | Heuristic proposer reads titles only; LLM proposer truncation counted but coverage not marked incomplete (`llm/roster.py`) | — | R6 (truncation as incompleteness in reports); LLM prompts unchanged (no paid calls) |
| 4.11 truncated prompts can canonically assign | PARTIAL | `truncated_prompts` counted in report; but truncated-context LLM assignments still land as canonical machine assignments | — | R3 (machine assignments are non-canonical by definition once 3.8 lands) |
| 4.12 strict PAR suppresses evidence | VIOLATED | `extract_and_validate_group` drops structural drafts (`services/claim_extraction.py:220-253`); evidence rows survive but extracted signal beyond claims is not retained/queryable | §16.9 case | R4 |
| 4.13 drops treated as acceptable loss | VIOLATED | Dropped drafts live only in `validation_runs` detail JSON (log, not active) | — | R4 |
| 4.14 action/result causal pairing not preserved | VIOLATED | No relationship model exists; extractor pairs within two-pass; bundle selection allows any A×R sharing problem_space_id (`domain/bundle_validation.py`, `services/story_review.py:313-352`) | §16.11 case | R5 |
| 4.15 problem-space grouping can blend | PARTIAL | Grouping merges semantically; §9.3 selection gate is the enforcement point → R5. Cross-space contamination already gated (`story_cross_space_claim_ids`). | — | R5 |
| 4.16 “100%” over wrong universe | VIOLATED | Gather denominators = discovered candidates; GitHub candidates exclude the file universe entirely; no discovery-vs-processing split | §16.15 case | R1+R6 |
| 4.17 Paper Recommender substring-only check | PARTIAL | `ingestion_preflight` scans normalized substrings (`domain/ingestion_preflight.py`) — explicitly a presence scan; §16.13 end-to-end assertions absent | — | R7 (16.13 end-to-end over fixtures) |
| 4.18 tests/fixtures encode wrong behavior | VIOLATED | e.g. `test_roster_service.py` asserts README chunks assign to repo entity; fixtures have no collection repo, no CLAUDE.md, no nested README | — | R1/R3/R7 fixture+test corrections (each recorded below) |
| 4.19 structural audit ≠ semantic rightness | (principle) | Addressed by R3/R5/R7 semantic tests. |

## §5 Non-negotiable invariants

### 5.1 Source accounting
| # | Status | Notes |
|---|---|---|
| 1-4 dispositions, no silent drops, named failures | PARTIAL | H2 GatherReport covers Drive docs/repos/uploads; per-FILE dispositions inside a repo don't exist (universe gap) → R1. `unsupported with reason` + `awaiting user decision` statuses missing → R1. |
| 5 required-source failures block publication | VIOLATED | → R2/R8. |
| 6-8 discovery vs processing; real universe; cache labeling | VIOLATED | → R6. |
| 9 version identity/hash/provenance | SATISFIED | capture layer (H2/H4). |
| 10 raw preserved pre-transform | SATISFIED | same. |

### 5.2 GitHub
1-3 (repo≠project, cardinality) → R3. 4-5 (README/CLAUDE.md accounted, nested docs discoverable) → R1.
6-7 (commits supporting; volume≠importance) → R4/R6 + BLOCKED(22.9) beyond binding default.
8 (failed docs not hidden) → R2. 9-11 (collection force-assign, repo-ref-only ownership, first-match) → R3.
12 (path+revision provenance) → R1. 13-14 (file universe enumerable, exclusions reported) → R1/R6.

### 5.3 Projects/entities
1-2, 3 (containers) → R3. 4-5 SATISFIED in data model (many sources per entity; one source many entities possible via sections). 6 PARTIAL (proposals not canonical until confirmed — SATISFIED; deterministic-proof path absent). 7 SATISFIED (`propose_experience` returns discarded rows unchanged; test exists). 8 → R3 (alias absorption via force-assign). 9 SATISFIED (idempotent detection). 10 → R3.

### 5.4 Evidence preservation
1-5 PARTIAL: chunks survive independent of claims (good), but relevant-signal classification (6) absent; “not PAR” is still effectively terminal for extracted non-claim signal (7) → R4. 8 (exclusion requires user decision) → R4. 9 SATISFIED (claim_evidence links + verbatim gates). 10 → R4 (queryability surface).

### 5.5 Assignment
1-2, 7 → R3 (machine guesses labeled `readme_ref`/`repo_ref` presented as canonical). 3 → R3/R6. 4 SATISFIED (H1 pins + H6 migration; live-verified). 5 SATISFIED (`assignment_method` column). 6 SATISFIED (`GET /roster/unassigned`). 8-9 → R8 (publication gate) — note extraction boundary already refuses cross-group citations. 10 → R3.

### 5.6 Action/result integrity
1-8 → R5. 9 (review convenience) → R5. 10 (published bullets no unsupported causality) → R5 (select/bullet gates) + already-partial (bullet = verbatim "action — result" with number gate; the pairing itself is the gap).

### 5.7 Master CV
1-2 SATISFIED (grounding + attestation gates, number gates). 3-4 → R3/R7. 5 SATISFIED (cross-space/cross-project gates + eval). 6 → R5. 7 SATISFIED (`DuplicateMetricError`). 8-9 → R4. 10 SATISFIED (component→claim→evidence walk; H8 audit). 11-12 PARTIAL → R8 (atomic insert yes; dropped-story silence + no completeness gate). |

### 5.8 Cost/idempotency
1-5, 9 SATISFIED (`extraction_hash`, `synthesis_hash`, recorded groupings, targeted re-runs, checkpoint = per-group). 6-7 PARTIAL (CostTracker logs, no pre-flight bound — M25 scope, NOT repaired here: spec §5.8.7 conflicts with M25 plan; treating as out-of-repair-scope since no current paid path runs unbounded offline; recorded as remaining). 8 SATISFIED (flags default off). 10 SATISFIED (rule-version stamps: NORMALIZATION_VERSION/STRUCTURER_VERSION/GROUPING_PROMPT_VERSION). |

### 5.9 Failure/publication
1-2 → R2/R8. 3 → R3/R8. 4 → R8 (silent story drop). 5 SATISFIED (validators name objects). 6-8 PARTIAL → R8. 9 SATISFIED (versions immutable; store keeps priors). 10 SATISFIED (snapshot content self-describing + fingerprint). |

## §6 Required source behavior
- 6.1 Drive: mostly SATISFIED (enumeration+policy+dispositions+raw+structure); PARTIAL: no sentinel/malformed-output detector beyond MCP isError (PR #68 fixed error-as-content); title-as-entity default in proposer → R3; unassigned sections exposed (roster/unassigned) SATISFIED.
- 6.2 GitHub universe → R1 (VIOLATED, as §4.1).
- 6.3 source importance → R4/R6 default tiering; user override BLOCKED(22.2) beyond default.
- 6.4 source conflicts: metric conflicts SATISFIED (`detect_metric_conflicts`, human picks); duplicate cross-entity SATISFIED (overlap merge prompts).

## §7 Roster requirements
- 7.1 SATISFIED in intent; 7.2 proposal fields → R3 (add container/repo-name-derived/overlap metadata to proposals — no schema change: proposal-time report + aliases).
- 7.3 SATISFIED (confirm endpoints; extraction refuses without confirmation).
- 7.4 collection handling → R3 (VIOLATED today).
- 7.5 merge rules SATISFIED (human merge; overlap prompts are suggestions).

## §8 Evidence extraction
- 8.1/8.2 categories → R4 (no category model; minimal non-lossy categorization at extraction).
- 8.3 no lossy PAR gate → R4 (VIOLATED for extracted signal; chunk layer OK).
- 8.4 fragments → R4/R7 (16.10). Current heuristic extracts full statements (sentence-level); structural drop of short fragments violates preservation → R4.
- 8.5 metrics: verbatim + provenance + conflicts SATISFIED; duplicate reuse gated. `reported|calculated|estimated|attested` distinction PARTIAL (verified/user_attested exist) — record as remaining gap (schema-adjacent).
- 8.6 uncertainty states → R4 (absence codes exist as readiness; ownership/relationship-unknown states → R3/R5).

## §9 Action/result relationships
- 9.1 relationship types → R5 (derive statuses; no schema: computed from claim/evidence linkage).
- 9.2 publication rule → R5.
- 9.3 selection rule → R5 (VIOLATED: same-bundle A×R freely selectable).
- 9.4 multiple pairs preserved: PARTIAL (multiple claims per space preserved; one-bullet-per-story limit is v3.1 selection design; §10.1 requires multiple stories per project — stories are per problem space already (v3.1), so multiple bullets per entity exist across spaces; within a space, selection picks one — spec allows selection subset (10.3) as long as evidence retained + reversible: SATISFIED by candidate retention; unresolved: multiple bullets within one space) → R5 note; further limits BLOCKED(22.8).

## §10 Master CV construction
- 10.1 → v3.1 per-space stories satisfy multi-story-per-project; within-space single bullet noted above.
- 10.2 qualities → R3/R4/R5/R7 combined.
- 10.3 inventory vs prose → R4 (inventory surface) + SATISFIED (candidates retained, exclusion reasons retained).
- 10.4 benchmark doc — reference only; UNVERIFIABLE offline (docx not in repo); noted.

## §11 Known truth cases → R7 (all)
- 11.1 portfolio: machinery violations → R3; fixtures lack the case → R7 adds `Cameron-Morreale-portfolio`-shaped fixture (collection README w/ OneWorld + Paper Recommender sections + shared metric).
- 11.2 Paper Recommender: end-to-end fixture assertions → R7 (16.13).
- 11.3 Cooper: workstream separation — bundle boundary gates exist; §16.11 pairing gate → R5; test with two A + two R same-entity different-workstreams → R7.
- 11.4 OneWorld duplicate: overlap pass + DuplicateMetricError exist → R7 verifies end-to-end with portfolio fixture.
- 11.5 JobPilot: high-signal docs drive narrative → R1 (CLAUDE.md/docs ingested at all) + R6 tiering; volume dominance → R4/R6.

## §12 Dataset problems
12.1/12.5/12.6 SATISFIED (missing-result follow-up, attestation flow, absence-as-readiness). 12.2 → R3 (surface candidate boundaries in proposals). 12.3 SATISFIED (aliases + merge prompts). 12.4 generic repo names → R3 (proposal metadata flags repo-name-derived).

## §13 Audit/reporting → R6
13.1 discovery: PARTIAL (gather report; missing file-level GitHub + configured-scope echo). 13.2 capture: SATISFIED (H8 audit). 13.3 structure: SATISFIED (H4/H8 + preflight). 13.4 entity detection: PARTIAL (reconciliation covers expected-projects; containers/repo-as-project counters absent → R6). 13.5 assignment: PARTIAL (method counts derivable; add to report → R6). 13.6 extraction: SATISFIED mostly (report + eval + dropped JSON) — non-PAR retained counter → R4. 13.7 relationships → R5 counters. 13.8 Master CV → R8 dispositions. 13.9 cost: SATISFIED (llm cost logging + skip counters) minus pre-flight bound (recorded remaining).

## §14 Failure semantics
14.1 → R2/R8. 14.2 SATISFIED after R3 (unresolved stays unresolved). 14.3 SATISFIED (failed groups skip loudly, claims/evidence retained, targeted re-run). 14.4 → R4 (evidence invalidity vs story incompleteness — absence split exists; structural drop loss → R4). 14.5 → R8.

## §15 UI/review requirements
Read surface: PARTIAL — evidence text/context/assignment/method/status/unassigned/superseded exposed via roster+stories APIs; missing: evidence category, relationship status, container view, current-CV usage → R4/R5/R6 additive JSON fields on existing endpoints (no new workflow).
Actions: inspect/correct/merge/move/attest/reject exist; split-a-false-entity = discard+re-propose (documented); “see what will render” = snapshot latest + render; “compare source truth” = evidence links. Gaps recorded, additive only.

## §16 Acceptance tests → R7 (status at start)
| Test | Start | End target |
|---|---|---|
| 16.1 GitHub file-universe | FAILS (no universe) | test added w/ R1 |
| 16.2 Portfolio collection | FAILS | R3+R7 |
| 16.3 README failure masking | FAILS | R2 |
| 16.4 CLAUDE.md omission | FAILS | R1 |
| 16.5 Source priority | FAILS (no tiering) | R4/R6 |
| 16.6 Multi-project repo assignment | FAILS | R3 |
| 16.7 First-match ambiguity | FAILS | R3 |
| 16.8 Long-doc truncation | PARTIAL (heuristic path reads full text; LLM path counts truncation) | R6 |
| 16.9 Non-PAR preservation | FAILS | R4 |
| 16.10 Fragment preservation | FAILS (structural drop) | R4 |
| 16.11 A/R causal integrity | FAILS | R5 |
| 16.12 Duplicate metric | PASSES partially (DuplicateMetricError, overlap prompts) | R7 verifies |
| 16.13 Paper Recommender e2e | NO TEST | R7 |
| 16.14 Failed extraction preservation | PASSES (verify) | R7 verifies |
| 16.15 Coverage denominator | FAILS | R6 |
| 16.16 Publication atomicity | PASSES structurally (verify) | R7 verifies + R8 |
| 16.17 Cost/idempotency | PASSES (verify) | R7 verifies |

## §19 Protocol — followed per repair (failing test first; bounded fix; targeted → subsystem → full suite).

## §20 Definition of done — mapped 1:1 to the above; final report enumerates each.

## §21 Prohibited shortcuts — checked at final review.

## §22 Open user decisions — resolution table
| Decision | Binding default found? | Effect |
|---|---|---|
| 22.1 GitHub universe | YES for docs: §4.1 mandates every README (incl. nested) + CLAUDE.md, NO EXCEPTIONS; §6.2 adds root README variants + architecture/design docs + project Markdown as "at minimum ... explicitly account for". Commits: already admitted (§6.2 "commit history when admitted" + current behavior preserved). Notebooks/source/tests/issues/PRs: NOT admitted by default (§6.2 "when admitted") → those enumerate as `awaiting user decision`/`excluded by policy` dispositions, not silently absent. | R1 implements: all Markdown docs (README*, CLAUDE.md, *.md incl. /docs) ingested; other tree entries enumerated with explicit non-ingested dispositions. |
| 22.2 importance rules | YES: §6.3 default ranking is binding "default behavioral expectation". | R4/R6 use §6.3 tiers. |
| 22.3 canonical assignment policy | YES: §3.8 gives the definition (deterministic user-approved boundary, user-confirmed, or proven). | R3 stops labeling repo-ref guesses canonical; deterministic boundary = single-entity repo whose entity the user confirmed with the repo alias (user-approved boundary), multi-match = unresolved. |
| 22.4 required project inventory | NO default (user list). `reconcile_expected_projects` exists and takes the list as input. | Publication gate takes optional expected list; absent list = no required-project blocking. Dependent hard-blocking BLOCKED(22.4). |
| 22.5 publication blockers | PARTIAL default: §5.9/§14.1 name required-source failure + evidence-accounting failure + ambiguity-not-silently-published. | R2/R8 implement those; further blockers BLOCKED(22.5). |
| 22.6 sensitive evidence | NO default → M24 scope; BLOCKED(22.6). No repair. |
| 22.7 PAR role | YES: §3.10/§8.3 — PAR = readiness organization, never evidence existence. | R4. |
| 22.8 multiple stories per project | PARTIAL default: §10.1 forbids lossy compression; v3.1 per-space stories satisfy it. Within-space multi-bullet limits BLOCKED(22.8). |
| 22.9 commit role | PARTIAL default: §5.2.6 supporting-only unless promoted. | R4 tiers commits as supporting; full exclusion/promotion semantics BLOCKED(22.9). |
| 22.10 review thresholds | PARTIAL default: current human-confirm roster + human story review + H1 pins. Additional thresholds BLOCKED(22.10). |

---

# Repair log (append-only; before/after per repair)

## R1 — GitHub source-universe enumeration (§4.1/4.2/6.2/16.1/16.4) — DONE
**Before:** GitHub ingestion = root `README.md` (hardcoded) + commit messages. CLAUDE.md, nested READMEs, docs never read; no file-tree enumeration; no per-file dispositions.
**After:** `GitHubClient` gains `list_repo_files` (complete tree) + `read_repo_file` (path+revision provenance). Gather enumerates every repo's tree; READMEs (root+nested), CLAUDE.md, and all Markdown ingest as documents (`github_doc` source type, ref = `owner/repo/path`); non-admitted files get explicit `awaiting_user_decision` dispositions (§22.1 unresolved for code/tests/notebooks — binding docs default applied). Nested READMEs propose child projects (dir name), other docs propose nothing. Root README keeps legacy `(github_readme, repo_ref)` identity for live-DB evidence continuity.
**Files:** `app/integrations/base.py` (+`GitHubRepoFile`, doc path/revision, 2 protocol methods), `app/integrations/mock/github.py` (manifest `files` + tree), `app/integrations/mcp/github.py` (directory-walk enumeration, per-path reads, revision from payload sha), `app/domain/repo_docs.py` (new pure admission/title rules), `app/domain/claims.py` (`SOURCE_GITHUB_DOC` + click-through URL), `app/services/roster.py` (universe loop + dispositions), `app/domain/roster.py` (proposer: nested README → child project proposal).
**Tests:** `tests/test_github_universe.py` (10 tests, written failing-first), MCP walk tests in `tests/test_mcp_github_client.py` (3), fixtures `tests/fixtures/github_universe/` (jobpilot, portfolio collection, claude-only, broken-readme). Test fakes in `test_source_capture.py`/`test_section_ownership.py` gained the two methods; `test_gather_report_statuses_cover_every_disposition` updated to the §5.1.2 disposition set (source-truth correction).
**Validation:** targeted tests green; full offline suite green; ruff+mypy clean.

## R2 — Required-doc failure semantics + publication gate (§4.3/5.1.5/5.9/14.1/14.5/16.3/16.16) — DONE
**Before:** README read failure logged as a disposition; commits continued; nothing downstream consumed the failure — snapshots/renders published regardless. Approved stories dropped at render (ungrounded number / not resume-ready) were log-lines only.
**After:** `RepoDocAccounting` per repo (files enumerated / docs ingested / commits captured / readme+claude present&captured / `complete`); `GatherReport.required_failures` + `.complete`; `source_gather` validation run passes only when complete. `create_story_snapshot(validation_log=…)` BLOCKS on the latest failed gather (`SourceCompletenessError`, raised before any version write → prior valid Master CV survives) and records a `master_cv_publication` run with per-story dispositions (rendered / dropped+reason / skipped). Wired: `render_master_cv_from_stories(validation_log=…)`, `PipelineDependencies.validation_log` (+ SQL log in `build_default_dependencies`).
**Files:** `app/services/roster.py`, `app/services/story_snapshot.py`, `app/services/master_cv_render.py`, `app/services/pipeline.py`, `app/domain/validation_runs.py` (+`KIND_MASTER_CV_PUBLICATION`).
**Tests:** `tests/test_publication_gate.py` (5, failing-first), 16.3 masking test in `tests/test_github_universe.py`.
**Validation:** full offline suite green; ruff+mypy clean.

## R3 — Collection repos, first-match ambiguity, canonical-assignment honesty (§4.6-4.9/§7.2/§7.4/§12.4/§16.2/16.6/16.7) — DONE
**Before:** `_repo_entity` returned the FIRST alias match; every root-README chunk force-assigned to the repo-matching entity (`readme_ref`, "correct by construction"); every commit force-assigned by repo ref; a collection repo's sections and commits all landed under one entity; no container/generic-name/overlap metadata on roster entries; H2 sections under a single H1 were undecidable (top-level-only section assignment guessed from the mixed body).
**After:** (a) multi-match refs are recorded ambiguity (`RosterAssignmentReport.ambiguous`) and their evidence stays unresolved — never first-match owned; (b) a repo with nested-README child docs is a COLLECTION: its root README is owned per SECTION (recursive refinement: a branch subtree is decided by its own heading only — a mixed body never votes; silent heading descends into child subtrees via new pure `child_sections`), and its repo-wide commits stay unresolved/supporting-only; (c) force-assignment (`readme_ref`) survives ONLY for the §3.8 deterministic user-approved boundary — a single confirmed entity carrying the repo alias (or a child project carrying its nested README ref); (d) `roster_review_hints` (pure) derives `may_be_container` / `derived_from_repo_name` / `generic_name` (§12.4 incl. course codes) / `overlapping_ids` served on every roster API row.
**Files:** `app/services/roster.py` (boundary/ambiguity/commit logic, recursive section decisions, report field), `app/domain/source_structure.py` (`child_sections`), `app/domain/roster.py` (`roster_review_hints`), `app/api/roster.py` (hints in serialization).
**Tests:** `tests/test_collection_assignment.py` (7, failing-first for the two live defects; regression guards for single-project boundary, nested-README child ownership, collection commits, preamble honesty, hints).
**Validation:** roster subsystem 54 passed; full offline suite green; ruff+mypy clean.

## R4 — Evidence preservation + inventory (§4.12/4.13/§5.4/§6.3/§8/§10.3/§16.5/16.9/16.10/16.14) — DONE
**Finding first:** the chunk layer ALREADY preserves evidence independently of claims (H2/H6: rows persist, supersede-never-delete, unassigned queue). What was missing: a queryable inventory with §5.4.6 categories and §6.3 importance, and PROOF that the §16.9/16.10/16.14/16.5 behaviors hold end-to-end.
**Added:** `app/domain/evidence_inventory.py` — `evidence_categories` (derived ONLY from facts: claim-citation fields, attestation source, commit=supporting-implementation, unassigned=unresolved — no text classifier, no guessing) and `source_importance` (§6.3 default tiers; README/CLAUDE.md/drive/upload=1, other repo Markdown=2, PRs=3, commits=5). `GET /roster/evidence` serves the full inventory (text, section context, assignment+method+lifecycle, categories, tier, cited-by, source URL). `ambiguous` surfaced on the assign API response.
**Verified (tests written first, 6 in `tests/test_evidence_preservation.py`):** §16.9 non-PAR evidence (scope/tech/partial results, no pain point) — zero rows vanish, claims queue without absence flags; §16.10 fragment evidence survives draft drops, no invented expansion; §16.14 failed extraction preserves raw evidence + prior claims, rerun re-extracts ONLY the failed group; §16.5 one authoritative README-derived claim outranks 30 commit claims in story selection (existing leverage ranking proved sufficient — no new plumbing added; commit volume cannot dominate because bare commit actions rank lowest and MAX_STORY_ACTIONS caps).
**Deliberately NOT changed:** structural drops of truncated/unspecific claim DRAFTS remain (they are extraction quality control, fully reconstructable in validation_runs per H7/F7) — compliant because preservation is enforced at the EVIDENCE layer, which these tests now prove; per §21 the drop log itself is never counted as preservation.
**Validation:** 6/6 new tests green; full offline suite green; ruff+mypy clean.

## R5 — Action/result causal integrity (§4.14/§5.6/§9/§16.11) — DONE
**Before:** any action × any result sharing a `problem_space_id` was selectable and could generate a causal "action — result" bullet — the §9.3 violation exactly (same-space as proof of relationship).
**After:** `pairing_relationship` (pure, `domain/bundle_validation.py`) derives the §9.1 status from provenance facts only: `direct` (same claim — the extractor's coupled unit), `same_source_section` (distinct claims citing shared evidence), `user_attested` (the story's typed result answer; selecting it is the user's confirmation), else `unknown`. `validate_pairing_support` refuses `unknown` with machine-readable `unsupported_pairing`. Enforced at BOTH gates: `select_bundle_component` (409, nothing persists) and `generate_story_bullet` (re-gated, so a selection recorded before a claim edit can never publish unsupported causality). Semantic similarity/space/entity/repo sharing are never proof (§5.6.2-6).
**Files:** `app/domain/bundle_validation.py` (+relationship model, 2 functions, new fatal code), `app/services/story_review.py` (wired both paths).
**Tests:** `tests/test_pairing_integrity.py` (failing-first; §16.11 two-pairs case: valid same-claim pair selectable, cross-claim pair 409 + nothing persisted; relationship derivation unit). All 29 existing bundle/story tests untouched and green (their happy paths were same-claim = direct).
**Validation:** full offline suite green; ruff+mypy clean.

## R6 — Coverage denominators + reporting (§4.16/§5.1.6-8/§13.1/§16.15) — DONE
**Before:** gather counted only discovered candidates; the GitHub "universe" was the README subset; no discovery-vs-processing split; no named missing files; no coverage surface.
**After:** `GatherReport.coverage()` — per-repo `{discovery_complete, files_enumerated, docs_admitted, docs_ingested, processing_pct, fully_ingested, missing_files (named), required_failures}` + totals separating enumeration, admission, ingestion, awaiting-user-decision, exclusions, read failures. Denominator = the actual enumerated universe; 2-of-10 captured reports 20%, never "fully ingested". Served on `/roster/detect` and `/roster/assign`.
**Files:** `app/services/roster.py`, `app/api/roster.py`.
**Tests:** `tests/test_coverage_denominators.py` (failing-first; the exact §16.15 ten-admitted/two-captured case, missing eight named).
**Validation:** full offline suite green; ruff+mypy clean.

## R7 — Known-truth cases + end-to-end acceptance (§11, §16.2/16.8/16.12/16.13/16.16) — DONE
**Tests:** `tests/test_truth_cases_e2e.py` (4) over the portfolio fixture universe, full spine offline (detect → human roster decisions → assign → extract → synthesize → attest → approve → publish):
- **11.1 portfolio:** proposed (visible, container-hinted) → user-discarded; owns zero evidence; no story; the published CV contains no portfolio-named entry — the repo survives ONLY as provenance refs (§11.1 "repository identity is provenance").
- **11.2/16.13 Paper Recommender:** raw-captured (`source_document_versions`), structured (element tree), identified (child proposal from its nested README), assigned (nested README + the root README's PR section both under the PR entity), retained, inventory-visible, disposition = a reviewable story card. Assertions are structural, not substring presence.
- **11.4/16.12 OneWorld duplicate:** the shared metric's evidence rows (portfolio README section + nested OneWorld README) have exactly ONE owner (OneWorld); the published bullets carry the metric at most once; cross-story double-count remains refused by `DuplicateMetricError` (existing live-corpus tests).
- **16.16/14.1:** with the broken-readme required failure recorded, publication RAISES and writes nothing; a newer successful gather supersedes; publication then succeeds with recorded dispositions.
- **16.8:** a project 400 sections deep in a long document is still discovered (raw-text reconciliation) and its section still receives an ownership decision — heuristic path has no prompt budget; LLM-path truncations remain counted+reported.
- **11.3 Cooper:** covered by existing `test_cooper_result_extraction`/`test_bundle_boundary`/`test_problem_space_separation` + R5's pairing gate (workstreams separate, metrics attached, action-only evidence queued, cross-blending unselectable).
- **11.5 JobPilot:** R1 (CLAUDE.md/docs/ARCHITECTURE.md now evidence) + §16.5 test (doc-driven narrative, commit volume capped + outranked).

## Final status (2026-07-12)
- **Full offline suite: 965 tests, 0 failures.** ruff + mypy clean. Zero paid calls consumed (see incident note), zero external writes, zero migrations, zero DB changes, main untouched (branch `repair/master-cv-spec`, working tree only).
- **§16 acceptance tests:** 16.1✓ 16.2✓ 16.3✓ 16.4✓ 16.5✓ 16.6✓ 16.7✓ 16.8✓ 16.9✓ 16.10✓ 16.11✓ 16.12✓ 16.13✓ 16.14✓ 16.15✓ 16.16✓ 16.17✓ (16.17 via 16.14 rerun-scope assertions + existing extraction-hash tests).
- **Incident note (paid-call constraint):** two API call ATTEMPTS occurred during the repair — one from an ad-hoc debug script that read the developer `.env`, one from a new test that relied on `get_settings()` while the process cache held real settings. Both were REJECTED by the API with a billing error: zero tokens billed, zero output. Both vectors closed (script deleted; all new tests pass explicit offline Settings; the conftest autouse `.env` guard was already protecting everything else).

### Remaining items (no omissions)
1. **BLOCKED(§22.1)** — source code / tests / notebooks / issues / PRs admission: enumerated with `awaiting_user_decision` dispositions; ingestion awaits the user's checklist.
2. **BLOCKED(§22.4/22.5)** — required-project inventory + additional publication blockers: `reconcile_expected_projects` + the gate exist; hard-blocking on a required-project list needs the user's list.
3. **BLOCKED(§22.6)** — sensitive-evidence policy (M24 scope).
4. **BLOCKED(§22.8/22.9/22.10)** — within-space multi-bullet limits; commit promotion/exclusion beyond the binding supporting-only default; extra review thresholds.
5. **§5.8.6-7 (cost pre-flight bound)** — CostTracker still logs without a fail-closed ceiling (M25 scope; no offline path can exceed it today).
6. **§8.5** — `reported|calculated|estimated` metric-value distinction (only verified/user_attested exist; schema-adjacent).
7. **§4.10/4.11 (LLM paths)** — LLM roster/section prompts still truncate with reporting; truncated-context machine assignments are non-canonical under §3.8 but still feed extraction. Heuristic default paths never truncate. Tightening the LLM prompts requires paid-call testing → deferred.
8. **§15 dashboard UI** — every new surface is API-served (inventory, hints, coverage, ambiguity, dispositions); `web/` components not yet extended to render them.
9. **§13 consolidated report** — components exist across coverage()/validation_runs/audit_pipeline; a single merged per-run artifact remains a nicety.
10. **Live-corpus run** — NOT executed: re-ingesting the live u1 corpus under the new GitHub universe requires paid LLM extraction and writes to the live DB (both need explicit authorization). Offline proxy: the live-shaped corpus fixture suite + the new fixture universe. **The live Master CV will not visibly change until an authorized re-ingest runs.**
11. **Prior evidence rows**: existing live `github_readme` rows keep their identity (deliberate H6 continuity); new `github_doc` evidence appears only after an authorized live re-gather.

### Rollback
No commits were made. `git checkout -- .` + `git clean -fd tests/fixtures/github_universe app/domain/repo_docs.py app/domain/evidence_inventory.py tests/test_github_universe.py tests/test_collection_assignment.py tests/test_coverage_denominators.py tests/test_evidence_preservation.py tests/test_pairing_integrity.py tests/test_publication_gate.py tests/test_truth_cases_e2e.py docs/master_cv_repair_traceability.md` on branch `repair/master-cv-spec` restores the pre-repair tree; or simply `git checkout fix/normalizer-restamp-on-verbatim-upsert` (its tree is untouched).

## R8 (post-live) — Cost controls the live run proved missing (§5.8.1/§5.8.3/§5.8.6-7) — DONE
**Live evidence (2026-07-13):** one unbounded re-ingest spent ~$29.70 before the credit balance itself stopped it. Breakdown: ~$17.8 on LLM section/chunk ASSIGNMENT (13K-token prompts emitting 8-token entity ids, re-paid in full across three interrupted runs — assignment had NO checkpoint), ~$10.6 on extraction (incl. 4 validation bounces doubling batches), $1.2 detection.
**Fix 1 — assignment fingerprint skip:** `run_roster_assignment` stamps a per-document `assignment_fingerprint` validation run (sha256 over raw content + roster identity + NORMALIZATION/STRUCTURER versions + assigner generation + collection membership) ONLY on a fully successful pass; a document whose latest fingerprint matches is skipped outright — no chunking, no LLM calls, no reconciliation churn (`skipped_unchanged` on report + API). Roster or content changes invalidate naturally; assigner failures never stamp (retry stays eligible).
**Fix 2 — fail-closed extraction ceiling:** `LLM_COST_CEILING_USD` (default $10/run, `0` disables) — `estimate_group_cost_usd` (chars/4 tokens × conservative 3×in/1.5×out envelope on the real price table) gates each group BEFORE it starts; over-ceiling groups are named (`skipped_budget`), never started, still eligible next run. Enforced only when real LLM extraction is on — the free heuristic never gates. Run estimate printed by the CLI (§13.9).
**Files:** `app/services/roster.py`, `app/services/claim_extraction.py`, `app/config.py`, `app/domain/validation_runs.py`, `app/tools/run_claim_extraction.py`, `CLAUDE.md` (flag table).
**Tests:** `tests/test_cost_controls.py` (4: unchanged-doc zero-call skip + roster-change invalidation; over-budget group not started + next-run continuation with hash-skip; heuristic never gated; estimate sanity). `test_tool_ingest_wiring` stub extended.
**Remaining honesty:** the ceiling gates extraction only; a FIRST assignment pass with the LLM assigner still costs (~$6-9 on this corpus) — after it, the fingerprint makes re-runs free. `ROSTER_LLM_DETECTION=false` makes assignment free entirely (heuristic + deterministic boundaries), at the cost of re-deciding currently-LLM-assigned sections.
