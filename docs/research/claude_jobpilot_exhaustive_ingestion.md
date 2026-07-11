# JobPilot: Exhaustive PDF/DOCX Ingestion Fix

## Mission

Fix the blocking ingestion defect in JobPilot.

The requirement is literal:

> Parse, preserve, and make available **everything readable** from every ingested `.pdf` and `.docx` file.

The current implementation silently omits visible source content. That is a correctness failure.

Do not stop after planning. Inspect the repository, reproduce the defect, write failing tests, implement the fix, re-ingest the actual files, inspect persisted records, verify the API and UI, and continue until the acceptance criteria pass.

---

## Scope

Fix the complete source-to-evidence path for:

- `.pdf`
- `.docx`

Also fix any downstream boundary that incorrectly hides evidence already present in the canonical source layer.

Do **not**:

- redesign the full PAR system
- build the complete second brain
- generate new resume bullets
- invent missing facts
- hardcode a specific project or filename
- treat LLM summaries as raw evidence
- discard content because it is unlinked or unclassified
- return only a plan

The solution must remain compatible with future second-brain ingestion.

---

## Core Invariant

For every successfully ingested document:

```text
all_detected_source_elements
=
persisted_source_elements
+ explicitly_unsupported_elements
+ explicit_parser_failures
```

Required:

```text
silently_dropped_elements = 0
```

Every detected source element must end in exactly one explicit state:

1. Persisted
2. Persisted but unresolved/unlinked
3. Explicitly unsupported, with reason
4. Parser failure, with error details

There is no silent omission state.

---

## What “Everything” Means

Preserve every readable source element, including:

- titles, headings, subheadings, section labels
- paragraphs
- bullets, numbered items, nested lists
- wrapped bullet lines
- tables, nested tables, merged-cell content
- headers and footers
- hyperlinks and visible labels
- text boxes and drawing text
- dates, metrics, technologies, accomplishments
- project and employer descriptions
- action and outcome statements
- captions, annotations, form-field text
- footnotes, endnotes, comments when accessible
- image-only or unsupported-page status
- blank-page status
- parser warnings and errors

Do not filter for “important,” “resume-worthy,” or “PAR-relevant” content during raw ingestion.

Preserve first. Interpret later.

---

## Required Architecture

Use a strict two-pass design.

### Pass 1: Exhaustive Source Capture

Pass 1 must:

- parse every readable source element
- preserve verbatim text
- preserve order
- preserve hierarchy
- preserve source location
- persist canonical source records
- record unsupported elements
- record parser failures
- reconcile counts
- fail loudly if anything is unaccounted for

No LLM may decide what survives Pass 1.

### Pass 2: Semantic Interpretation

Only after Pass 1 reconciles successfully may downstream logic:

- detect sections and projects
- resolve entities
- classify Problem, Action, Result, or Other
- create derived claims
- generate resume bullets
- cluster or rank evidence

Pass 2 failures must never erase, overwrite, or hide Pass 1 records.

---

## Canonical Data Contract

PDF and DOCX parsers must emit the same canonical representation.

Use the existing schema if sufficient. Otherwise add an equivalent model with at least:

```text
SourceDocument
- id
- source_type
- original_filename
- mime_type
- content_hash
- byte_size
- parser_name
- parser_version
- ingestion_run_id
- ingestion_status
- page_count / section_count
- created_at

SourceDocumentVersion
- id
- source_document_id
- content_hash
- parser_version
- ingestion_run_id
- created_at
- is_active

SourceElement
- id
- source_document_version_id
- parent_element_id nullable
- element_type
- sequence_index
- page_number nullable
- section_index nullable
- paragraph_index nullable
- table_index nullable
- row_index nullable
- column_index nullable
- list_level nullable
- bounding_box nullable
- raw_text
- normalized_text
- style_metadata
- source_locator
- content_hash
- extraction_status
- parser_warning nullable
- parser_error nullable
- created_at

ProjectEvidenceLink
- source_element_id
- project_id nullable
- association_method
- association_confidence
- review_status
```

Every source element must preserve:

1. verbatim text
2. normalized text
3. document order
4. hierarchy
5. structural type
6. source location
7. provenance
8. parser version
9. ingestion run
10. explicit disposition

---

## Raw Evidence Boundary

Enforce this throughout the codebase:

```text
raw source text != generated interpretation
```

The following are **not** raw evidence:

- LLM summaries
- generated PAR claims
- generated resume bullets
- generated project descriptions
- previous JobPilot outputs
- normalized paraphrases

Rules:

- `raw_text` must be verbatim.
- `normalized_text` may normalize whitespace, line endings, and Unicode only.
- Model-generated content must be stored separately.
- Derived claims must reference supporting `SourceElement` IDs.
- Derived content must never replace raw source records.

---

## Required Repository Trace

Before modifying code, trace the actual path:

```text
file upload
→ file-type detection
→ parser selection
→ raw extraction
→ layout reconstruction
→ chunking
→ semantic extraction
→ filtering
→ deduplication
→ project/entity linking
→ persistence
→ retrieval
→ API serialization
→ UI rendering
```

Identify every place where content can be:

- filtered
- truncated
- summarized
- overwritten
- deduplicated
- dropped on validation failure
- hidden by linking failure
- hidden by classification failure
- omitted by pagination
- excluded by top-N limits
- swallowed by exception handling

Do not speculate. Use actual code paths, database rows, logs, and tests.

---

## PDF Requirements

The PDF path must handle:

- native-text PDFs
- single-column layouts
- multi-column resumes
- sidebars
- wrapped bullets
- multiple bullets under one project
- tables
- headers and footers
- reordered PDF object streams
- mixed text/image pages
- blank pages
- encrypted or malformed PDFs
- scanned or image-only pages
- annotations and form fields when readable

For native-text PDFs:

- extract every available text span
- preserve page number
- preserve coordinates and style metadata when available
- retain original low-level spans
- reconstruct reading order deterministically
- detect columns instead of trusting raw object order
- preserve bullet markers and wrapped-line relationships
- never silently skip a page

If spans are grouped into paragraphs or bullets, retain both:

- original span records
- grouped derived records

For scanned/image-only pages:

- explicitly detect the condition
- use OCR only when supported and reliable
- mark OCR-derived text
- preserve confidence and bounding boxes when available
- never report complete ingestion when a readable page produced no text and no explicit disposition

---

## DOCX Requirements

Parse the complete WordprocessingML package.

Do not rely only on:

```python
document.paragraphs
```

Inspect and preserve:

- body paragraphs
- headings
- runs, tabs, line breaks, page breaks
- bullets, numbered lists, nested lists
- tables, nested tables, merged cells
- headers and footers for all sections
- hyperlinks
- text boxes and drawing text
- footnotes and endnotes
- comments where supported
- content controls when accessible
- unsupported embedded objects with explicit disposition

Preserve true document order. Paragraphs and tables must not be collected into separate lists that destroy sequence.

Use XML traversal where necessary.

---

## Reconciliation

Add document-level and page/section-level reconciliation.

Track at least:

```text
filename
source_type
content_hash
parser_name
parser_version
pages_expected
pages_processed
sections_expected
sections_processed
raw_elements_detected
elements_persisted
unsupported_elements
parser_warnings
parser_errors
unresolved_elements
linked_elements
silently_dropped_elements
ingestion_status
```

If reconciliation fails:

- mark ingestion failed
- preserve partial records
- persist the mismatch
- expose it through logs or API
- prevent downstream synthesis for that document version
- do not mark ingestion successful

---

## No Silent Exception Handling

Find and eliminate patterns equivalent to:

```python
except Exception:
    pass
```

```python
except Exception:
    return []
```

```python
except:
    continue
```

Every parser exception must be:

- logged with context and stack trace
- associated with the ingestion run
- associated with the document/page/element when possible
- persisted as an explicit parser error
- visible in reconciliation
- covered by tests

---

## Deduplication Rules

Raw ingestion must not silently deduplicate source content.

If deduplication exists:

- run it only after raw capture
- never delete original source records
- retain provenance for all duplicates
- distinguish exact duplicates from similar text
- preserve repeated bullets, headers, and footers
- never remove distinct bullets because they share vocabulary

Prefer retaining duplicates over deleting unique evidence incorrectly.

---

## Project and Entity Linking

Source preservation must not depend on project resolution.

Rules:

- unlinked evidence remains persisted
- uncertain evidence remains unresolved
- failed project matching never erases text
- exact title equality is not required for preservation
- semantic similarity alone must not force a link
- evidence must not leak across projects, users, repositories, or documents

Support aliases, but preserve unresolved evidence rather than forcing weak associations.

---

## Downstream State Semantics

The application must distinguish:

- no raw evidence
- raw evidence exists but is unlinked
- raw evidence exists but is unclassified
- no explicit Problem found
- no Action selected
- no explicit Result found
- classification not run
- parser failed
- ingestion failed

“This project has no evidence” may appear only when ingestion succeeded, reconciliation passed, and zero relevant raw source elements exist.

A missing Problem must not cause Actions to disappear.

A failed classifier must not make the UI report that source evidence is absent.

---

## Idempotency and Second-Brain Readiness

Implement or preserve:

- stable document IDs
- content hashes
- element hashes
- parser-version tracking
- ingestion-run IDs
- idempotent re-ingestion
- version-aware reprocessing
- no duplicate active records for unchanged files
- batch persistence
- bounded memory usage
- deterministic ordering
- pagination
- retryable failures
- audit history
- tombstone/deactivation handling for replaced versions

PDF and DOCX must normalize into the same canonical `SourceElement` contract.

Downstream PAR logic must not depend on source-specific schemas.

---

## Required Execution Sequence

1. Inspect the repository.
2. Trace both ingestion paths.
3. Reproduce the defect using the actual source file when available.
4. Write failing regression tests.
5. Implement exhaustive source capture.
6. Add reconciliation and failure gating.
7. Re-ingest from original source files.
8. Inspect persisted records directly.
9. Verify API output.
10. Verify UI behavior.
11. Run the full relevant test suite.
12. Continue until all acceptance criteria pass.

Do not infer correctness from the UI alone.

---

## Required Tests

### PDF fixtures

Cover:

- single-column PDF
- multi-column resume
- sidebar layout
- wrapped bullets
- multiple bullets under one project
- tables
- headers and footers
- repeated text
- mixed text/image page
- image-only page
- blank page
- malformed/encrypted PDF
- reading-order ambiguity

Assert exact counts, text, order, page, type, locator, and disposition.

### DOCX fixtures

Cover:

- title and headings
- plain paragraphs
- bullets and nested bullets
- numbered and nested numbered lists
- tables and nested tables
- merged cells
- headers and footers
- hyperlinks
- line breaks and tabs
- section breaks
- text boxes/drawing text
- footnotes/endnotes/comments
- unsupported embedded objects

Assert every expected element is persisted.

### Persistence and reconciliation

Prove:

- every parsed element receives a record
- sequence and hierarchy are preserved
- raw text remains verbatim
- normalized text does not paraphrase
- warnings/errors are retained
- re-ingestion is idempotent
- changed files are reprocessed correctly
- `silently_dropped_elements == 0`

### Downstream boundaries

Prove:

- unclassified evidence remains visible
- unlinked evidence remains visible
- missing Problem does not erase Actions
- failed classification does not erase raw evidence
- API pagination does not hide later evidence
- UI “no evidence” is based on raw source existence

---

## Concrete Regression: Paper Recommender System

Use the actual source document.

From the original file:

- enumerate every visible source element in the Paper recommender section
- persist every element verbatim
- preserve document order and source location
- show resulting `SourceElement` IDs
- link them to the correct project or surface them explicitly as unresolved
- verify none were omitted
- verify Actions is not zero when action statements exist
- verify the project is not labeled “no evidence” when source evidence exists

Do not copy text from this specification into the database. Extract it from the actual file.

---

## Prohibited Shortcuts

Do not:

- increase chunk size and call that a fix
- increase token limits and call that a fix
- summarize the entire document with an LLM
- store only model-selected evidence
- extract only “important” or “resume-worthy” content
- keep only the first N elements
- keep only one action or result
- drop unclassified or unlinked content
- silently deduplicate similar text
- silently skip pages, tables, headers, footers, text boxes, or hyperlinks
- mark partial ingestion as successful
- hardcode one project or file
- weaken tests
- stop after producing a design document
- claim success without inspecting actual persisted records

---

## Acceptance Criteria

The task is complete only when:

1. Every readable source element in every tested PDF and DOCX has an explicit persisted disposition.
2. Verbatim text, order, structure, and provenance are preserved.
3. Every page or section has an explicit processing disposition.
4. `silently_dropped_elements == 0`.
5. Reconciliation passes exactly.
6. Parser failures and unsupported elements are auditable.
7. PDF and DOCX emit the same canonical source contract.
8. The Paper recommender bullets are all present.
9. The Paper recommender project no longer reports Actions: 0 when actions exist.
10. The project no longer reports “no evidence” when source evidence exists.
11. Re-ingestion is idempotent.
12. Raw evidence and derived claims are structurally separate.
13. Downstream classification cannot erase raw evidence.
14. Cross-project contamination is prevented by tests.
15. Relevant automated tests pass.
16. Database, API, and UI outputs are verified directly.
17. No project-specific hardcoding was introduced.
18. The implementation remains second-brain compatible.

---

## Final Response Format

Return only these sections:

### 1. Root Causes

For each root cause:

- exact file
- exact function/class
- exact failure mechanism
- content affected
- stage where loss occurred

### 2. Code Changes

Concise file-by-file summary, including migrations and tests.

### 3. Parsing Verification

For every tested document:

```text
filename:
file_type:
content_hash:
parser:
parser_version:
pages_expected:
pages_processed:
sections_expected:
sections_processed:
raw_elements_detected:
elements_persisted:
unsupported_elements:
parser_warnings:
parser_errors:
unresolved_elements:
linked_elements:
silently_dropped_elements:
ingestion_status:
```

### 4. Paper Recommender Verification

For every extracted element:

```text
source_element_id:
exact_raw_text:
source_locator:
sequence_index:
project_link_state:
project_id:
classification_state:
```

Then report:

```text
visible_source_elements:
persisted_source_elements:
omitted_source_elements:
silently_dropped_elements:
```

### 5. Database Verification

Show the actual queries or ORM checks used.

### 6. API and UI Verification

Report endpoint, evidence count, pagination behavior, project state, and whether “no evidence” is still displayed incorrectly.

### 7. Tests

```text
commands_run:
tests_passed:
tests_failed:
tests_skipped:
pdf_cases_covered:
docx_cases_covered:
reconciliation_cases_covered:
idempotency_cases_covered:
downstream_boundary_cases_covered:
```

### 8. Remaining Defects

List only concrete unresolved defects.

Do not include vague recommendations or unsupported claims of success.

---

Begin now by inspecting the repository and tracing the current `.pdf` and `.docx` ingestion paths.

Do not stop after planning.

Implement the fix, run the tests, re-ingest the actual files, inspect persisted records, and keep working until the acceptance criteria pass.
