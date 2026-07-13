# MASTER CV PIPELINE REPAIR SPECIFICATION 

**Owner:** Cam Morreale  
**Status:** Governing user-authored requirements  
**Scope:** Google Drive and GitHub ingestion through evidence extraction, project assignment, career-signal synthesis, review, and Master CV publication  
**Architecture authority:** The user only  
**Agent role:** Execute bounded work against this specification. Do not reinterpret it.

---

## 0. Authority and precedence

This document is the authoritative behavioral specification for the Master CV pipeline.

When this document conflicts with any of the following, this document wins:

- `README.md`
- `CLAUDE.md`
- plans and architecture documents
- source-code comments
- tests and fixtures
- seeded data
- current database state
- previous prompts
- previous agent decisions
- previous pull requests
- current behavior

Existing behavior is not presumed correct merely because it is documented, tested, or deployed.

A green test suite is not proof of correctness when the tests encode a known-wrong interpretation of the userâs career history or requirements.

### 0.1 This is not an architecture document

This document defines:

- required observable behavior;
- prohibited behavior;
- invariants;
- failure semantics;
- acceptance tests;
- known source-truth cases;
- known defects.

It does not authorize an AI agent to:

- redesign the architecture;
- invent a new domain model;
- add abstractions;
- add migrations;
- replace subsystems;
- change persistence semantics;
- redefine project identity;
- decide what correct means.

Those decisions belong to the user unless explicitly delegated in writing.

### 0.2 Agent operating rules

Any AI agent working on this repository must:

1. Read this file in full before analysis or modification.
2. Treat the requirements as final for the scoped task.
3. Identify the exact current behavior that conflicts with the requirement.
4. Add or update tests proving the user-defined behavior.
5. Implement only the minimum bounded correction requested.
6. Stop and ask when a requirement is genuinely ambiguous.
7. Never fill ambiguity with an invented product decision.
8. Never broaden scope without explicit authorization.
9. Never change fixtures merely to make incorrect behavior appear correct.
10. Never weaken assertions to obtain a green suite.
11. Never claim success based only on unit tests.
12. Validate behavior against the actual representative corpus and known truth cases.
13. Report every unresolved discrepancy.
14. Preserve existing user-approved decisions unless explicitly revoked.
15. Avoid paid calls, external writes, destructive migrations, and full-corpus reruns unless explicitly authorized.

---

# 1. Product objective

The Master CV pipeline must:

1. Read the user’s admitted Google Drive and GitHub corpus.
2. Account for every source object in the configured source universe.
3. Preserve raw source material before interpretation.
4. Identify career-relevant signals without inventing information.
5. Distinguish storage boundaries from real-world career entities.
6. Identify real employers, roles, projects, products, research efforts, hackathons, and other meaningful bodies of work.
7. Associate each relevant piece of evidence with the correct real-world entity.
8. Preserve relevant evidence even when it is incomplete, ambiguous, non-PAR-shaped, or not immediately resume-ready.
9. Construct a truthful, detailed, source-grounded Master CV.
10. Make every consequential claim traceable to exact source evidence or explicit user attestation.
11. Expose ambiguity rather than silently guessing.
12. Allow the user to review and correct decisions that cannot be resolved honestly from the source corpus.
13. Avoid repeating unchanged paid work.
14. Fail loudly when required source coverage or evidence integrity is incomplete.
15. Publish only a complete, validated candidate state.

The core product expectation is:

> Read the user’s Drive and GitHub, extract relevant career evidence, assign it to the correct real-world projects and roles, and construct an exhaustive, defensible Master CV without losing, mixing, suppressing, relabeling, or inventing evidence.

---

# 2. Complaints captured as requirements

## 2.1 Excessive failure count

The pipeline currently has too many failures across ingestion, source interpretation, project detection, assignment, extraction, validation, synthesis, and rendering.

Required behavior:

- Every failure must be observable, attributable, reproducible, and bounded.
- A failure in one stage must not be mislabeled as success in a later stage.
- “The tests passed” is not acceptable when the visible output is materially wrong.
- The system must provide a clear source-to-output trace for representative failures.

## 2.2 Codebase growth without product improvement

The codebase has grown substantially while the visible Master CV has not improved proportionally.

Required behavior:

- Every repair must demonstrate visible improvement against a named defect.
- Added code is not progress by itself.
- Validators, migrations, and audits are not substitutes for correct output.
- A repair is not complete until source-to-Master-CV behavior improves on the real acceptance corpus.
- Agents must report before/after product behavior, not only implementation changes.

## 2.3 Unauthorized product decisions

The implementation introduced decisions the user never authorized, including:

- prioritizing implementation-level evidence over project documentation;
- guessing project ownership;
- dropping evidence that does not fit strict PAR;
- pairing actions and results without a supported relationship;
- defining “100% ingestion” over an incomplete universe. 100% ingestion means the raw data is a 100% match with what was ingested into jobpilot. NO EXCEPTIONS, anything less is unacceptable;
- allowing incomplete source reads to proceed;
- encoding known-wrong interpretations into fixtures and tests.

Required behavior:

> Any behavior that deletes, suppresses, relabels, merges, assigns, ranks, or reinterprets source evidence must be explicitly authorized by this specification or the user.

When behavior is unspecified, preserve the evidence and surface the ambiguity.

## 2.4 Current output is unacceptable

The user considers the current output far below the product promise.

This reflects failures in:

- source selection;
- signal quality;
- entity identity;
- evidence assignment;
- career relevance;
- narrative coherence;
- completeness;
- trustworthiness;
- usefulness as a Master CV.

Required behavior:

- Judge visible output against source truth, not only internal schemas.
- Low-level implementation details must not overwhelm high-level career value.
- Project identity must be correct.
- Evidence must be organized under the correct entities.
- The Master CV must make the actual body of work clearer.

## 2.5 Dataset limitations do not justify guessing

Some source material may be incomplete, ambiguous, inconsistent, or missing explicit labels.

Required behavior:

- Identify dataset gaps as dataset gaps.
- Identify parser failures as parser failures.
- Identify assignment ambiguity as assignment ambiguity.
- Do not reclassify missing evidence as system success.
- Do not fabricate certainty.
- Ask the user or leave evidence unresolved when source material cannot support a canonical decision.
- A dataset limitation is not permission to guess.

---

# 3. Binding definitions

## 3.1 Repository

A GitHub storage and version-control boundary.

A repository is not automatically:

- a project;
- an employer;
- a role;
- a resume entry;
- a Project Story;
- a Master CV section.

It may represent one project, many projects, a portfolio, a monorepo, documentation, infrastructure, a library, experiments, coursework, archives, or mixed content.

## 3.2 Project

A real-world body of work meaningful as a career entity.

A project may occupy:

- one repository;
- part of one repository;
- multiple repositories;
- one or more Drive documents;
- a combination of Drive and GitHub;
- no repository.

A project is defined by the work, not by a file or repository boundary.

## 3.3 Collection repository

A repository containing multiple distinct projects.

A collection repository:

- is not automatically a project;
- must not appear as a project merely because it is a repository;
- may contribute evidence to several child projects;
- may have a root README functioning as a table of contents;
- may contain directories or headings defining separate projects.
-may contain nested readmes (where the career evidence is)

`Cameron-Morreale-portfolio` is the canonical example.

## 3.4 Employer role

A real employment, internship, contract, research, or other professional role associated with an organization and period.

A file describing the role is not itself the role.

Multiple files may describe one role.

## 3.5 Career evidence

Any source-grounded information that may help describe professional capability or contribution.

Career evidence includes:

- project purpose;
- problem context;
- research objective;
- user need;
- business or technical constraint;
- scope;
- responsibility;
- action;
- implementation;
- design or architecture decision;
- collaboration;
- leadership;
- deliverable;
- result;
- metric;
- scale;
- reliability, quality, or coverage improvement;
- automation;
- capability unlocked;
- technology;
- method;
- evaluation;
- publication;
- competition result;
- stakeholder context;
- product strategy;
- operating principle.

Evidence does not need to be a complete resume bullet. Do not extract just one word, the whole sentence or sentences related to that artifact must be extracted with no exceptions.

## 3.6 High-signal career source

A source intentionally explaining work, context, decisions, outcomes, or project identity.

Examples:

- `README.md`;
- `CLAUDE.md`;
- project Markdown;
- architecture/design documents;
- technical/project reports;
- notebooks with narrative and results;
- portfolio descriptions;
- case studies;
- pull-request descriptions;
- issue summaries;
- existing CVs and resumes;
- user notes.

## 3.7 Supporting implementation source

A source proving or elaborating implementation but not necessarily expressing career impact.

Examples:

- source code;
- tests;
- configuration;
- schemas;
- commit messages;
- code comments;
- dependency files;
- generated artifacts.

Supporting sources must be retained but must not automatically dominate the narrative.

## 3.8 Canonical assignment

An evidence-to-entity assignment allowed for downstream Master CV synthesis.

A canonical assignment must be:

- deterministic from explicit user-approved boundaries;
- explicitly confirmed by the user;
- or otherwise proven under user-approved rules.

An unresolved machine guess is not canonical.

## 3.9 Unresolved evidence

Relevant evidence whose ownership, relationship, or interpretation cannot be established honestly.

Unresolved evidence must remain visible and queryable.

It must not be discarded, force-assigned, hidden in logs, or treated as irrelevant.

## 3.10 PAR

Problem Action Result.

PAR is one possible organization of evidence for resume writing.

PAR is not:

- the canonical evidence store;
- permission to delete evidence;
- a complete definition of career relevance;
- a requirement that every source sentence contain all three elements;
- a reason to suppress research, scope, responsibilities, technologies, or artifacts.

## 3.11 Master CV

An exhaustive, user-approved, source-grounded representation of the user’s career evidence.

It is broader than a tailored one-page resume.

---

# 4. Known implementation conflicts

## 4.1 GitHub ingestion is incomplete

Observed behavior:

- The GitHub adapter hardcodes root `README.md`.
- It fetches commit messages.
- It does not enumerate the complete repository tree.
- It does not ingest `CLAUDE.md`.
- It does not ingest nested READMEs.
- It does not ingest architecture docs unless they are the root README.
- It does not ingest notebooks or reports from the repository.
- It does not ingest PRs or issues through the Master CV path.
- It does not establish a complete repository file manifest.

Conflict:

The product is described as ingesting GitHub projects, but the implemented universe is effectively repository metadata + root README + commit messages.

Required behavior:

The configured GitHub universe must be explicitly enumerated and accounted for. The system must not claim complete GitHub ingestion when it only reads part of the root README and commits. It must do an exhaustive search over every README (included nested ones) and CLAUDE.md. NO EXCEPTIONS. Failures are unacceptable.

## 4.2 `CLAUDE.md` is ignored

Required behavior:

- Discover and ingest `CLAUDE.md` when present and admitted.
- Report its presence or absence.
- Read failure is always unacceptable. If failure is present, it must be addressed immediately.
- Retain repository/path/revision provenance.
- Make its content available as career evidence.

## 4.3 README failure can be masked by commit success

Observed behavior:

- README read can fail.
- Commit ingestion continues.
- Output can become dominated by implementation history while missing the primary narrative.

Required behavior:

- Required documentation failure must block publication or mark the candidate incomplete.
- Commit success must not mask failed README or `CLAUDE.md` retrieval.
- Distinguish repository discovered, documentation captured, history captured, and ingestion complete.

## 4.4 Commit messages are overrepresented

Observed behavior:

- Commit messages are first-class Action/Result evidence.
- Commit-heavy repositories generate large evidence groups.
- One README competes with many commits. README should dominate, that is the primary source of evidence.

Required behavior:

- Retain commits as supporting evidence.
- Do not let commit volume dominate stronger documentation.
- Preserve source importance separately from source count.
- Commit evidence must not override explicit project identity or documented context.

## 4.5 Repositories are proposed as projects

Required behavior:

- A repository must not automatically become a project.
- Repository-to-project cardinality must remain unresolved until supported.
- Support zero, one, or many projects per repository and many repositories per project.

## 4.6 `Cameron-Morreale-portfolio` is treated as a project

Required behavior:

- It must not appear as a project in the canonical roster or Master CV.
- Its contents must contribute to child projects where appropriate.
- Repository identity may remain as provenance, not project identity.
- Fixtures and tests treating it as a project must be corrected.

## 4.7 README evidence is force-assigned to the repository entity

Required behavior:

- Repository ownership and project ownership must not be conflated.
- Root README sections describing child projects must be assignable to those projects.
- Collection repositories must not force all sections into one entity.

## 4.8 Commits are force-assigned to the repository entity

Required behavior:

- Repository reference alone is insufficient canonical ownership in a collection repository.
- Ambiguous commits remain unresolved or supporting-only.
- Commit evidence must not reinforce a false portfolio project.

## 4.9 First-match alias behavior can decide ownership

Required behavior:

- Canonical assignment may not depend on arbitrary first-match ordering.
- Multiple matches create ambiguity.
- Ambiguity must be surfaced.
- No force assignment merely because one match appeared first.

## 4.10 Roster detection sees truncated documents

Required behavior:

- Do not claim complete roster detection from partial prefixes.
- Truncation must be visible and treated as incomplete coverage.
- Required projects must not be considered absent until complete admitted sources are examined.

## 4.11 Chunk and section assignment can use truncated prompts

Required behavior:

- Truncated context cannot justify canonical assignment unless explicitly approved.
- Truncation must either preserve deterministic ownership context or leave evidence unresolved.
- “Reported truncation” is not equivalent to " safe truncation”.

## 4.12 Strict PAR suppresses usable evidence

Required behavior:

- Preserve raw and normalized evidence.
- Keep relevant evidence queryable and reviewable.
- “Does not form complete PAR” is not a valid terminal disposition.
- PAR may determine readiness, not evidence existence.

## 4.13 Structural claim drops are treated as acceptable loss

Required behavior:

- A reconstructable log is not equivalent to active, queryable evidence.
- Non-PAR evidence must remain available for reassignment, reclassification, future synthesis, manual review, and alternative narrative structures.

## 4.14 Action/result causal pairing is not preserved

Required behavior:

- Pair only when source evidence or user confirmation supports the relationship.
- Distinguish direct, indirect, uncertain, unrelated, and user-attested relationships.
- Unknown relationships remain unknown.
- Reject arbitrary cross-pairing.

## 4.15 Problem-space grouping can still blend work

Required behavior:

- Semantic similarity alone is not sufficient proof of same workstream.
- Grouping must not expand evidence ownership or action/result compatibility beyond source support.
- Keep uncertain items separate or ask the user.

## 4.16 “100% ingestion” measures the wrong universe

Required behavior:

- The denominator must be the actual configured source universe.
- Separate discovery coverage from processing coverage.
- Explicitly report repositories, files enumerated, files admitted, exclusions, failures, captured objects, structured objects, evidence items, unresolved items, assignments, and publication blockers.

## 4.17 Paper Recommender presence is only a weak substring check

Success requires:

1. Source discovered.
2. Raw source captured.
3. Relevant section or file structured.
4. Project identified as a distinct real-world project.
5. Evidence assigned to that project.
6. Relevant evidence survives extraction.
7. It appears in the evidence inventory.
8. Its Master CV disposition is visible.
9. It is not absorbed into the portfolio.
10. Provenance is complete.

Phrase presence alone is not success.

## 4.18 Tests and fixtures encode known-wrong behavior

Required behavior:

- Fixtures must encode source truth, not current bad state.
- Known-wrong states should be negative controls.
- Tests must fail when a collection becomes a project, a child project disappears, evidence is assigned to a container, required docs are skipped, incomplete coverage is called 100%, or non-PAR evidence disappears.
-jobpilot evidence must be cross validated against the raw data. Below 100% = unacceptable, tests should fail the jobpilot evidence is not a 100% match with the raw data in the drive + GitHub + markdown files. 
## 4.19 Structural audit strength does not compensate for semantic wrongness

Required behavior:

- Preserve semantic correctness as well as structural integrity.
- A traceable wrong assignment is still wrong.
- A reconciled incomplete universe is still incomplete.
- A provenance-complete false project is still false.

---

# 5. Non-negotiable invariants

## 5.1 Source accounting

1. Every discovered source object ends in one explicit disposition.
2. Allowed dispositions:
   - ingested;
   - excluded by user-approved policy;
   - read failed;
   - unsupported with reason;
   - awaiting user decision.
3. No source silently disappears.
4. Every read failure names the source and reason.
5. Required-source failures block publication.
6. Discovery and processing are separate metrics.
7. 100% means 100% of the actual configured universe.
8. Cached subsets must not be mislabeled as the live universe.
9. Every source version used downstream has identity, retrieval time, hash, provenance, path/reference, and extraction status.
10. Raw source is preserved before lossy transformation.

## 5.2 GitHub

1. Repository is not automatically project.
2. Repository may map to zero, one, or many projects.
3. Project may map to one or many repositories.
4. README and `CLAUDE.md` are explicitly accounted for.
5. Nested docs are discoverable when admitted.
6. Commits are supporting evidence unless user promotes them.
7. Volume does not determine importance.
8. Failed README/`CLAUDE.md` cannot be hidden by commit success.
9. Collection repos cannot force all evidence into one project.
10. Repo-reference matching alone cannot canonically assign child-project evidence.
11. Multiple matches create ambiguity, not first-match ownership.
12. Evidence retains path and revision provenance where available.
13. Repository file universe is explicitly enumerable.
14. Exclusions are reported.

## 5.3 Projects/entities

1. Every canonical project is a real-world project.
2. File names, repo names, and document titles are not automatically entities.
3. A portfolio container is not a project.
4. Multiple sources may describe one entity.
5. One source may describe multiple entities.
6. Proposals are not canonical until confirmed or deterministically proven.
7. Discarded false entities do not silently reappear.
8. Confirmed entities may not absorb unrelated evidence through alias overlap.
9. Identity is stable across reruns.
10. Ambiguity is visible.

## 5.4 Evidence preservation

1. Every relevant evidence item survives ingestion.
2. Relevant evidence is not deleted for missing Problem, Action, or Result.
3. Relevant evidence is not deleted for being short or incomplete.
4. Failed claim extraction does not invalidate evidence.
5. Failed PAR validation does not invalidate evidence.
6. Evidence may be context, scope, responsibility, problem, action, result, metric, method, artifact, technology, collaboration, leadership, supporting implementation, unresolved, duplicate with canonical link, or user-excluded.
7. âNot PARâ is not a valid terminal disposition.
8. Excluding relevant evidence requires explicit user decision or user-approved rule.
9. Every transformed claim traces to exact evidence.
10. Evidence remains queryable independently of story readiness.

## 5.5 Assignment

1. Every canonical assignment is correct.
2. Machine uncertainty is not disguised as certainty.
3. Incomplete/truncated context cannot create canonical assignment without approval.
4. Human-confirmed assignments are never overwritten.
5. Assignment method and status are preserved.
6. Unassigned evidence remains visible.
7. Ambiguous evidence remains unresolved.
8. Wrong assignment blocks publication.
9. Cross-project contamination is impossible in published output.
10. Repository ownership does not imply project ownership.

## 5.6 Action/result integrity

1. Actions and Results remain separate until relationship is supported.
2. Same project is insufficient proof.
3. Same employer is insufficient proof.
4. Same repository is insufficient proof.
5. Same problem space is insufficient proof.
6. Semantic similarity is insufficient proof.
7. Pair only through explicit source linkage, deterministic user-approved boundary, or user confirmation.
8. Unknown pairing remains unknown.
9. Do not optimize review convenience through unsupported pairings.
10. Published bullets may not imply unsupported causality.

## 5.7 Master CV

1. Every consequential statement is grounded or user-attested.
2. No invented employers, projects, dates, technologies, metrics, results, or responsibilities.
3. No false portfolio project.
4. No omitted required project.
5. No cross-project mixing.
6. No unsupported action/result pairing.
7. No duplicate metrics falsely presented as independent outcomes.
8. No relevant evidence lost solely because it is not resume-ready.
9. Construct from the full approved evidence inventory, not only a narrow PAR subset.
10. Every statement traces to exact source evidence.
11. Publication is atomic.
12. Failed candidates cannot modify the last valid Master CV.

## 5.8 Cost and idempotency

1. No unchanged paid semantic work is repeated.
2. Deterministic discovery/reconciliation precedes paid extraction.
3. Content hashes and relevant rule versions govern recomputation.
4. Failed runs do not trigger unrelated full-corpus reruns.
5. Work resumes from checkpoints.
6. Paid calls are countable and attributable.
7. Bound cost before execution.
8. No paid calls without authorization where not already granted.
9. Fixing one defect does not repeatedly reprocess unrelated sources.
10. Cache reuse must not preserve stale semantics after a rule change.

## 5.9 Failure and publication

1. Required-source incompleteness blocks publication.
2. Evidence-accounting failure blocks publication.
3. Assignment ambiguity does not silently publish.
4. Failed extraction does not publish incomplete output as complete.
5. Failed validation names failing objects.
6. Candidate state is isolated.
7. Canonical publication occurs only after required gates pass.
8. Publication is atomic.
9. Prior valid state remains available.
10. Every publication has reproducible manifest and provenance.

---

# 6. Required source behavior

## 6.1 Google Drive

The Drive pipeline must:

- enumerate the configured admitted universe;
- report all discovered documents;
- read supported admitted documents;
- preserve raw extracted text and metadata;
- distinguish Docs, PDFs, DOCX, Markdown, and text;
- report extraction failures;
- detect malformed or sentinel outputs;
- avoid treating failed extraction as empty content;
- preserve structure where available;
- repair PDF extraction damage without destroying provenance;
- allow one document to contain multiple projects or roles;
- avoid treating title as canonical entity by default;
- avoid using only a document prefix for all entity decisions;
- expose unassigned sections;
- preserve user-confirmed section ownership.

## 6.2 GitHub

The pipeline must support an explicitly defined source universe.

At minimum, explicitly account for:

- repository identity;
- default branch;
- root README variants;
- `CLAUDE.md`;
- nested README files;
- project Markdown;
- architecture/design docs;
- notebooks or reports when admitted;
- source and tests as supporting evidence when admitted;
- commit history when admitted;
- issues and PRs when admitted;
- repository paths;
- exact revisions or hashes.

This section mandates observable completeness and provenance, not a particular architecture.

## 6.3 Source importance

Default behavioral expectation:

1. Explicit project docs and user-authored career summaries carry strongest identity and narrative signal.
2. Architecture/design docs carry strong system-design evidence.
3. Reports, notebooks, issues, and PR descriptions can carry strong action/result evidence.
4. Code and tests provide implementation proof.
5. Commits and comments provide supporting detail.

The user may override this ranking.

## 6.4 Source conflicts

When sources conflict:

- preserve both;
- identify the conflict;
- do not silently choose;
- do not merge incompatible metrics;
- do not present both as independent accomplishments when they describe one event;
- ask the user when canonical resolution is needed.

---

# 7. Roster requirements

## 7.1 Roster purpose

The roster is a list of real-world career entities, not files, repositories, document titles, headings, or arbitrary model outputs.

## 7.2 Proposal requirements

Each proposal must show:

- name;
- type;
- supporting evidence;
- source paths;
- aliases;
- competing interpretations;
- whether it may be a container;
- whether it overlaps another proposal;
- whether it was derived only from a repository name.

## 7.3 Confirmation

A proposal becomes canonical only through explicit user confirmation or deterministic user-approved rules.

## 7.4 Collection handling

For collection repositories:

- represent container as source container, not career project;
- identify child projects;
- preserve project boundaries;
- do not arbitrarily assign shared infrastructure;
- repository-wide commits may remain unresolved/supporting-only;
- container name does not render as a project unless explicitly chosen.

## 7.5 Merging and deduplication

Merge entities only when they are the same real-world entity.

Shared tools, metrics, or text are insufficient alone.

Duplicate evidence across a portfolio and child project is usually evidence of a container relationship, not a reason to merge projects.

---

# 8. Evidence extraction requirements

## 8.1 Goal

Extract career-relevant signals, not only complete resume bullets.

## 8.2 Categories

Preserve, at minimum:

- project identity;
- role identity;
- date;
- scope;
- problem;
- objective;
- constraint;
- responsibility;
- action;
- result;
- metric;
- deliverable;
- capability;
- architecture/design decision;
- implementation detail;
- technology;
- methodology;
- evaluation;
- collaboration;
- leadership;
- stakeholder interaction;
- publication;
- competition outcome;
- operating principle;
- unresolved evidence;
- supporting evidence.

## 8.3 No lossy PAR gate

The pipeline may assess whether evidence supports a PAR narrative.

It may not delete, hide, exclude from future synthesis, or mark irrelevant merely because evidence lacks a complete PAR structure.

## 8.4 Fragment handling

Meaningful fragments must survive.

Examples:

- âRestored date coverage.â
- âFive Snowflake tables.â
- âDesigned DAO governance.â
- âTop 3 out of 100+ teams.â
- âIPFS/Filecoin architecture.â
- âPaper recommender system.â

A fragment may need context. It may not be dropped solely for length. You extract the entire sentence or sentences in the chunk related to or referencing the fragment instead of only the fragment.

## 8.5 Metrics

Metrics must:

- preserve exact source wording;
- retain provenance;
- distinguish reported, calculated, estimated, and user-attested values;
- avoid unsupported normalization;
- avoid duplicate reuse;
- expose conflicts;
- remain linked to the event measured.

## 8.6 Uncertainty

Support explicit states for:

- result missing;
- problem missing;
- relationship unknown;
- ownership uncertain;
- metric conflict;
- source incomplete;
- source failed;
- relevant but not yet interpretable.

Unknown is acceptable. Guessing is not.

---

# 9. Action/result relationship requirements

## 9.1 Relationship types

Preserve statuses such as:

- explicit direct relationship;
- same source section with direct narrative linkage;
- same work item with strong deterministic linkage;
- user-confirmed;
- probable but unconfirmed;
- same workstream only;
- unrelated;
- unknown.

## 9.2 Publication rule

Only direct, deterministic user-approved, or user-confirmed relationships may generate a bullet implying causality.

## 9.3 Selection rule

Do not offer arbitrary Action/Result combinations merely because they share a problem-space ID.

Invalid combinations must be unselectable or rejected.

## 9.4 Multiple actions and results

- Preserve independently.
- Preserve explicit pairings.
- Permit multiple truthful bullets.
- Do not force one-action/one-result simplification when it loses evidence.
- Do not combine unrelated work.
- Do not reuse one result for every action.

---

# 10. Master CV construction

## 10.1 No single-story-per-project constraint

Complex projects and roles may require multiple stories or bullets.

Do not compress when doing so mixes workstreams, loses accomplishments, hides results, creates false relationships, or reduces a role to one narrow problem.

## 10.2 Required output qualities

The Master CV must be:

- exhaustive;
- truthful;
- source-grounded;
- organized by real-world entity;
- readable;
- professionally useful;
- technically specific;
- outcome-aware;
- explicit about uncertainty;
- traceable;
- free from invented content;
- free from false container projects;
- free from low-level noise dominating the narrative.

## 10.3 Inventory versus prose

- All relevant evidence remains available.
- Rendered prose may select a subset.
- Selection is reversible and traceable.
- Unrendered evidence is not deleted.
- The user can inspect excluded or unselected evidence.

## 10.4 Reference benchmark

`Cam_Morreale_Master_CV_Full_Body_of_Work(2).docx` is a reference for the target class of output.

It demonstrates distinct employers/projects, detailed technical actions, verified metrics, independent project entries, portfolio treated as repository rather than substantive project, broad body-of-work coverage, and higher-order synthesis.

It is a benchmark, not automatically perfect.

---

# 11. Known truth cases

## 11.1 `Cameron-Morreale-portfolio`

### Source truth

- It is a collection of multiple projects.
- It is not one cohesive career project.
- Its README may repeat metrics and descriptions from child projects.
- Repository identity is provenance, not project identity.

### Required behavior

- Do not render it as a project.
- Do not create a Project Story for it.
- Do not assign all README sections to it.
- Do not assign all commits to it canonically.
- Extract child projects separately.
- Treat repeated evidence as duplicate/supporting evidence for the child project.
- Preserve repository as source container.

### Forbidden behavior

- Portfolio appears as canonical project.
- OneWorld evidence appears under portfolio.
- Paper Recommender evidence appears under portfolio.
- Duplicate portfolio metrics become separate accomplishments.
- Repository alias overrides section-level identity.

### Pass condition

The final Master CV contains real child projects and excludes the portfolio as a career project.

## 11.2 Paper Recommender System

### Source truth

- It is a real project.
- It appears in the admitted corpus.
- It must not be silently omitted.
- It may appear inside a portfolio or multi-project document.

### Required behavior

- Discover it.
- Capture its source.
- Preserve heading/path context.
- Create or match correct project identity.
- Assign evidence correctly.
- Retain evidence without full PAR.
- Surface missing information as a targeted question.
- Include it in the evidence inventory.

### Forbidden behavior

- Phrase presence alone counts as success.
- It is absorbed into portfolio.
- It is marked missing because source was truncated.
- It disappears for lacking a Result.
- Preflight is green while it has no evidence.

### Pass condition

The system shows exact supporting source evidence, correct assignment, and Master CV disposition.

## 11.3 Cooper.ai

### Source truth

Distinct workstreams include:

- FedEx shipping recovery;
- UPS history restoration;
- Laufer freight ingestion;
- Pacifica automation;
- JML;
- inventory;
- forecast;
- Klaviyo;
- other logistics/data work.

### Required behavior

- Keep all evidence under Cooper.ai.
- Keep workstreams separate.
- Do not mix FedEx, Pacifica, Laufer, or other components.
- Preserve metrics with correct workstream.
- Do not discard action-only evidence.
- Do not require every item to contain a business pain point.

### Pass condition

Multiple truthful Cooper bullets with correct relationships and no cross-workstream blending.

## 11.4 OneWorld duplicate in portfolio

### Required behavior

- Treat portfolio mention as duplicate/supporting evidence for OneWorld.
- Do not create portfolio accomplishment.
- Do not infer two entities share one result.
- Preserve both provenance sources if useful.

### Pass condition

Result appears once under OneWorld and never under a portfolio project.

## 11.5 JobPilot

### Required behavior

- Prioritize README, `CLAUDE.md`, and architecture/product docs.
- Use commits and code as supporting proof.
- Do not let hundreds of commits create an incoherent low-level entry.
- Preserve multiple accomplishments where appropriate.
- Do not reduce the project to one bug or narrow problem.

### Pass condition

The JobPilot section reflects product purpose, system design, reliability work, provenance controls, user-control boundaries, and measurable outputs.

---

# 12. Dataset problem requirements

## 12.1 Missing source evidence

- Identify what is missing.
- Identify likely resolving source.
- Ask a targeted question.
- Preserve existing evidence.
- Do not invent missing fields.

## 12.2 Ambiguous project boundaries

- Surface candidate boundaries.
- Show headings/paths.
- Do not force ownership.
- Request confirmation.

## 12.3 Conflicting names

- Preserve aliases.
- Do not duplicate project.
- Show candidate merge.
- Require confirmation when uncertain.

## 12.4 Generic repository names

Names such as `portfolio`, `final-project`, `DS4635`, `project`, `capstone`, or `assignment` are not canonical project names without source support.

## 12.5 Missing results

Produce:

- preserved Action evidence;
- explicit missing-result state;
- targeted follow-up;
- no fabricated outcome;
- no deletion.

## 12.6 Missing problems

Projects may still be relevant without an explicit business pain point.

Preserve research objective, opportunity, user need, technical challenge, assignment goal, product vision, competition prompt, regulatory constraint, or system limitation.

---

# 13. Audit and reporting requirements

Every full validation run must report:

## 13.1 Discovery

- configured Drive scope;
- configured GitHub scope;
- repositories discovered;
- Drive docs discovered;
- repository files enumerated;
- admitted files;
- excluded files and reasons;
- unsupported files;
- read failures;
- missing required files;
- duplicate source objects.

## 13.2 Capture

- raw objects captured;
- hashes verified;
- active/superseded versions;
- missing raw payloads;
- provenance completeness.

## 13.3 Structure

- objects structured;
- coverage failures;
- element counts;
- truncation events;
- parser failures;
- word-shredding indicators;
- fragment anomalies;
- unprocessed sections.

## 13.4 Entity detection

- proposed, confirmed, discarded, merged entities;
- unresolved container relationships;
- repositories proposed as projects;
- expected projects missing;
- expected projects present but not parsed.

## 13.5 Assignment

- evidence total;
- canonically assigned;
- human-confirmed;
- deterministic;
- machine-proposed only;
- unresolved;
- conflicting;
- truncated-context assignments;
- cross-project violations;
- container-repo assignments.

## 13.6 Extraction

- evidence examined;
- relevant items retained;
- items classified;
- extraction failures;
- non-PAR retained;
- claims created/incomplete/rejected;
- exact reasons;
- underlying evidence availability.

## 13.7 Relationship integrity

- direct links;
- user-confirmed links;
- uncertain links;
- rejected pairings;
- cross-workstream pairings prevented.

## 13.8 Master CV

- entities represented;
- expected entities missing;
- grounded bullets;
- attested bullets;
- unresolved blockers;
- duplicate metrics;
- unsupported claims;
- portfolio violations;
- publication status.

## 13.9 Cost

- deterministic work;
- cache reuse;
- paid calls proposed/executed;
- tokens;
- cost;
- retries;
- skipped unchanged groups;
- failed calls;
- recomputation due to rule-version changes.

---

# 14. Failure semantics

## 14.1 Required-source failure

Examples:

- README unreadable;
- `CLAUDE.md` expected but unreadable;
- repository enumeration incomplete;
- Drive extraction sentinel;
- required project source not captured.

Required behavior:

- mark candidate incomplete;
- block publication;
- preserve prior valid state;
- name exact source and failure;
- do not present partial output as complete.

## 14.2 Assignment failure

- Leave unresolved.
- Show competing entities.
- Do not publish under a guess.
- Allow user correction.

## 14.3 Extraction failure

- Preserve raw/normalized evidence.
- Preserve prior approved output.
- Identify failed group.
- Avoid destructive replacement.
- Avoid heuristic substitution unless authorized.

## 14.4 Validation failure

- Distinguish evidence invalidity from story incompleteness.
- Preserve evidence.
- Block only unsupported publication.
- Do not erase signal.

## 14.5 Publication failure

- No partial canonical update.
- Prior version remains valid.
- Candidate artifacts remain inspectable.
- Exact blockers are reported.

---

# 15. UI and review requirements

The interface must show:

- source object;
- source path;
- source type;
- exact evidence text;
- surrounding context;
- project/entity assignment;
- assignment method;
- assignment status;
- unresolved alternatives;
- evidence category;
- action/result relationship;
- duplicate/conflict status;
- inclusion/exclusion status and reason;
- source link;
- current Master CV usage.

The user must be able to:

- inspect all evidence for an entity;
- inspect unassigned evidence;
- inspect container repositories;
- correct project identity;
- split a false entity;
- merge aliases;
- move evidence;
- mark supporting-only evidence;
- attest missing context;
- reject unsupported pairings;
- see what will render;
- compare source truth with final output.

---

# 16. Required acceptance tests

## 16.1 GitHub file-universe test

Input includes:

- `README.md`;
- `CLAUDE.md`;
- `/docs/ARCHITECTURE.md`;
- nested project READMEs;
- source code;
- tests;
- commits.

Pass:

- configured files enumerated;
- each has disposition;
- README and `CLAUDE.md` captured;
- exclusions listed with reasons;
- commits counted separately from files;
- denominator equals configured universe;
- missing required doc fails run.

## 16.2 Portfolio collection test

Input: root README with multiple projects including Paper Recommender and OneWorld.

Pass:

- portfolio is not a project;
- child projects proposed separately;
- evidence stays with correct child;
- duplicate child metrics do not become portfolio accomplishments;
- rendered Master CV excludes portfolio container.

## 16.3 README failure masking test

Input: README fails, commits succeed.

Pass:

- repository ingestion incomplete;
- publication blocked;
- commit success does not imply completeness;
- failure visible.

## 16.4 `CLAUDE.md` omission test

Input: strongest evidence exists only in `CLAUDE.md`.

Pass:

- discovered;
- captured;
- assigned;
- available for extraction;
- visible in provenance;
- omission fails completeness.

## 16.5 Source-priority test

Input: one authoritative README and 100 low-level commits.

Pass:

- identity/narrative come from README;
- commits support;
- volume does not dominate;
- low-level noise does not replace project story.

## 16.6 Multi-project repository assignment test

Pass:

- evidence not force-assigned to container;
- Project A evidence cannot appear under B;
- shared infrastructure unresolved/supporting-only;
- commits not automatically assigned to container.

## 16.7 First-match ambiguity test

Input: two entities share repo alias.

Pass:

- no arbitrary first match;
- unresolved assignment;
- publication blocked for ambiguous evidence;
- user decision requested.

## 16.8 Long-document truncation test

Input: required project appears after prompt budget.

Pass:

- project still discovered;
- truncation not treated as complete;
- no false missing result;
- full source accounted for.

## 16.9 Non-PAR preservation test

Input: scope, responsibilities, architecture, methods, technologies, deliverables, partial results, no explicit pain point.

Pass:

- every relevant item remains visible;
- zero disappear for lacking PAR;
- evidence supports later synthesis;
- user can inspect it.

## 16.10 Fragment preservation test

Pass:

- meaningful fragments survive;
- context attachment attempted/requested;
- no drop solely by length;
- no invented expansion.

## 16.11 Action/result causal-integrity test

Input: two actions and two results in same broad space, only specific valid pairings.

Pass:

- invalid cross-pairings rejected;
- only supported pairings selectable;
- unknown remains unknown;
- bullets do not imply unsupported causality.

## 16.12 Duplicate metric test

Input: same result in child project and portfolio summary.

Pass:

- one canonical accomplishment;
- both sources may support;
- no double count;
- no portfolio project.

## 16.13 Paper Recommender end-to-end test

Pass:

- discovered;
- captured;
- structured;
- identified;
- assigned;
- retained;
- visible in inventory;
- not absorbed into portfolio;
- Master CV disposition visible;
- phrase-only presence insufficient.

## 16.14 Failed extraction preservation test

Pass:

- raw evidence remains;
- prior approved output remains;
- no empty replacement;
- failure visible;
- rerun resumes only failed work.

## 16.15 Coverage denominator test

Input: ten admitted files, two captured.

Pass:

- discovery can be 100% only if all ten enumerated;
- processing is 20%, not 100%;
- repository not called fully ingested;
- missing eight files named.

## 16.16 Publication atomicity test

Pass:

- canonical state unchanged after candidate failure;
- prior CV remains downloadable;
- candidate artifacts inspectable;
- no mixed old/new publication.

## 16.17 Cost/idempotency test

Input: unchanged corpus with one failed group.

Pass:

- completed groups reused;
- only failed/changed group retried;
- no repeated paid work;
- cost report identifies exact scope.

---

# 17. Edge-case template

```markdown
## EDGE-CASE-[ID]: [Name]

### Source setup
Describe exact Drive/GitHub files, structure, text, metadata, and relationships.

### Source truth
State the correct real-world interpretation.

### Current wrong behavior
State what the pipeline currently does.

### Required behavior
State the exact expected result.

### Forbidden behavior
State what must never happen.

### Ambiguity policy
State whether the system should:
- decide deterministically;
- leave unresolved;
- ask the user;
- exclude by explicit policy.

### Required provenance
State exact source references that must survive.

### Acceptance assertions
List binary pass/fail assertions.

### Publication effect
State whether failure blocks:
- evidence publication;
- entity confirmation;
- story synthesis;
- Master CV publication;
- all downstream work.

### Cost constraints
State whether the test must run:
- deterministically;
- without network;
- without paid calls;
- incrementally;
- with checkpoint reuse.
```

---

# 18. Edge-case categories to expand

## 18.1 Repository structure

- monorepo;
- portfolio;
- nested projects;
- shared library plus projects;
- archived projects;
- submodules;
- renamed repo;
- fork with original work;
- private repo;
- deleted repo;
- empty repo;
- README absent;
- multiple README variants;
- `CLAUDE.md` only;
- conflicting root and nested docs;
- generated docs;
- duplicate project folders;
- branch-specific docs;
- project moved between repos.

## 18.2 Document structure

- long PDF;
- word-per-line PDF;
- two-column resume;
- no blank lines;
- malformed headings;
- nested bullets;
- tables;
- scanned pages;
- mixed languages;
- duplicate pages;
- repeated headers/footers;
- truncated extraction;
- sentinel error text;
- multiple projects in one doc;
- one project across many docs;
- conflicting resume versions.

## 18.3 Entity identity

- repo name differs from project name;
- acronym aliases;
- employer and product share name;
- project renamed;
- multiple roles at one employer;
- one contract serving several clients;
- coursework versus project;
- paper versus software project;
- portfolio mention versus primary project;
- hackathon prototype became startup;
- school project continued independently.

## 18.4 Evidence assignment

- heading names project but body mentions another;
- shared technologies;
- shared metrics;
- generic text;
- boilerplate;
- copied README section;
- cross-repo duplicate;
- project-specific commit in monorepo;
- shared infrastructure commit;
- merge commit;
- generated commit message;
- commit authored by someone else;
- group contribution ambiguity.

## 18.5 Career relevance

- implementation detail only;
- architecture rationale;
- research objective;
- failed experiment;
- negative result;
- partial implementation;
- abandoned prototype;
- class assignment;
- volunteer work;
- personal project with professional relevance;
- sensitive personal project;
- healthcare-adjacent work;
- confidential employer work;
- metric without context;
- responsibility without result;
- result without explicit action.

## 18.6 Action/result relationships

- one action produces many results;
- many actions produce one result;
- result reported later elsewhere;
- team result with unclear contribution;
- repeated metric;
- conflicting result versions;
- correlation without causality;
- result inferred from system state;
- user attestation required;
- action and result in different files.

## 18.7 Publication

- incomplete required project;
- unresolved evidence;
- conflicting metric;
- stale source;
- superseded evidence;
- revoked attestation;
- source deleted after approval;
- renamed entity;
- edited boundary;
- partial candidate;
- renderer failure;
- duplicate bullets;
- excessive detail;
- sensitive content.

---

# 19. Agent task protocol

## Phase A: diagnosis only

- Read this specification.
- Identify exact conflicting code paths.
- Identify tests/fixtures encoding wrong behavior.
- No architecture proposal.
- No code changes.
- No paid calls.
- No database changes.
- Report smallest failing case.

## Phase B: failing tests only

- Add smallest realistic failing test.
- Use user-defined source truth.
- Do not alter production behavior.
- Do not alter fixtures to match wrong output.
- Verify failure for expected reason.

## Phase C: bounded implementation

- Implement only specified correction.
- Do not redesign adjacent systems.
- Do not broaden change.
- Do not weaken tests.
- Stop and ask if architecture must change.

## Phase D: validation

Run:

- targeted tests;
- full offline suite;
- representative real corpus;
- source-to-output trace;
- before/after comparison;
- cost report;
- unresolved ambiguity report.

## Phase E: user review

Report:

- files changed;
- assumptions removed;
- tests added;
- behavior before/after;
- real-corpus evidence;
- remaining failures;
- migrations/backfills, if any;
- rollback;
- confirmation no unrelated decisions were introduced.

---

# 20. Definition of done

## 20.1 Source completeness

- Drive universe fully enumerated.
- GitHub universe fully enumerated.
- README and `CLAUDE.md` accounted for.
- Every admitted source has disposition.
- Required failures block publication.
- Coverage denominator reflects actual configured universe.

## 20.2 Correct project identity

- No repo automatically treated as project.
- Portfolio is not a project.
- Paper Recommender is distinct.
- Known projects are not missing.
- Containers, projects, roles, and source files are not conflated.
- Canonical roster is confirmed or proven under approved rules.

## 20.3 Correct assignment

- Every published item belongs to correct entity.
- Uncertainty remains unresolved.
- Human corrections survive.
- No first-match assignment.
- No collection-repo force assignment.
- No cross-project contamination.

## 20.4 Evidence preservation

- No relevant evidence lost for failing PAR.
- Non-PAR evidence queryable.
- Fragments available.
- Failed extraction preserves evidence.
- Every exclusion has reason and authorization.

## 20.5 Relationship integrity

- Unsupported Action/Result pairing impossible in publication.
- Multiple workstreams separate.
- Metrics attached to correct events.
- Unknown causality not implied.

## 20.6 Master CV quality

- Represents actual body of work.
- High-signal docs drive narrative.
- Implementation details support rather than dominate.
- Projects and employers are distinct.
- Major accomplishments present.
- No invented content.
- No false portfolio project.
- No missing Paper Recommender.
- Every statement traceable.
- Output visibly better than current state.

## 20.7 Operational integrity

- No unnecessary paid work.
- Deterministic preflight before paid extraction.
- Candidate state isolated.
- Atomic publication.
- Failed runs preserve prior state.
- Audits prove structural and semantic correctness against truth cases.

---

# 21. Prohibited shortcuts

The following are not acceptable:

- rewording the complaint;
- redefining âprojectâ to fit current data;
- making every repo a project proposal and forcing user cleanup;
- calling README/commit coverage complete GitHub ingestion;
- treating substring presence as Paper Recommender success;
- counting dropped-draft logs as preserved evidence;
- treating traceable wrong assignment as correct;
- using same problem space as proof of Action/Result relationship;
- letting synthetic fixtures override source truth;
- adding audits without improving visible output;
- creating new architecture without user authorization;
- broadening repair scope;
- silently making paid calls;
- rerunning full corpus when one group changed;
- declaring fixed because tests are green;
- treating user disgust as cosmetic.

---

# 22. Open user decisions

## 22.1 Exact GitHub universe

Select:

- [ ] all Markdown
- [ ] all text
- [ ] notebooks
- [ ] source code
- [ ] tests
- [ ] issues
- [ ] pull requests
- [ ] release notes
- [ ] commits
- [ ] wiki
- [ ] other: __________________

## 22.2 Source importance rules

Define priorities:

____________________________________________________________________

## 22.3 Canonical assignment policy

Define when assignment may become canonical without manual review:

____________________________________________________________________

## 22.4 Required project inventory

List every required project:

____________________________________________________________________

## 22.5 Publication blockers

Define unresolved states that block publication:

____________________________________________________________________

## 22.6 Sensitive evidence policy

Define evidence that may be stored but not rendered:

____________________________________________________________________

## 22.7 PAR role

Define exactly how PAR should be used:

____________________________________________________________________

## 22.8 Multiple stories per project

Define limits or selection rules:

____________________________________________________________________

## 22.9 Commit role

- [ ] excluded
- [ ] supporting-only
- [ ] eligible for Actions
- [ ] eligible for Results
- [ ] eligible only with user confirmation
- [ ] other: __________________

## 22.10 Human review thresholds

Define which decisions require confirmation:

____________________________________________________________________

---

# 23. Final governing statement

The purpose of JobPilot is not to create an internally consistent approximation of the userâs career history.

The purpose is to create a truthful, complete, evidence-grounded representation of the userâs actual work.

The system must remain faithful to source evidence and explicit user decisions.

It must not:

- invent;
- silently omit;
- silently merge;
- silently relabel;
- silently assign;
- silently suppress;
- silently reinterpret.

When certainty is unavailable, preserve the evidence and ask.

When a source fails, fail loudly.

When the source is complete, extract the career signal actually present.

When the Master CV is published, the user must be able to trust every project, claim, metric, and relationship.