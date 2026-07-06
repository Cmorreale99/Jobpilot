# Repo-Native Second Brain Blueprint

## Purpose

This repo will become the operating system for a personal second brain rather than a forked template. The core idea is simple:

- Obsidian is the local knowledge surface and ingestion layer.
- Claude and MCP are assistants that help structure, query, and refine memory.
- JobPilot remains the downstream compiler that turns approved personal context into career stories, CV content, tailored outreach, and interview prep.

The second-brain build should not compete with Obsidian. It should make Obsidian more trustworthy, structured, and reusable for the workflows that matter.

## Design principle

This is not “a second-brain app copied from somewhere else.”
It is a repo-native memory system with:

- a local-first trust boundary,
- explicit provenance and approval states,
- typed memory objects instead of raw notes-only storage,
- review workflows before anything becomes canonical,
- a clean path for JobPilot to consume only approved evidence.

## Target architecture

### 1. Obsidian as the raw substrate

Obsidian remains the user-facing capture layer.
Notes live in a vault, with markdown, frontmatter, tags, and wikilinks as the normal input format.

The app should ingest from that vault through a narrow adapter that:

- reads markdown files,
- parses YAML frontmatter,
- resolves wikilinks and tags,
- normalizes note text,
- assigns metadata such as source, date, and sensitivity,
- writes structured memory records into the repo.

### 2. Repo as the memory system

The repository becomes the durable memory backend.
It stores typed objects such as:

- notes
- entities
- memories
- evidence links
- approvals and review decisions
- project stories
- derived summaries

This makes the second brain queryable, testable, and composable instead of being just a folder of markdown.

### 3. Claude + MCP as assistants, not truth sources

Claude and MCP are used to assist with:

- note classification,
- entity extraction,
- summarization,
- cross-linking,
- retrieval support,
- drafting review prompts.

They never overwrite approved memory directly.
Every AI-generated suggestion must pass through a review and approval layer.

## Proposed module layout

```text
app/
  second_brain/
    domain/
      note.py
      memory.py
      entity.py
      evidence.py
      approval.py
    services/
      vault_ingestion.py
      memory_index.py
      review_workflow.py
    integrations/
      obsidian.py
      claude.py
    api/
      notes.py
      memories.py
      review.py
```

## Core workflow

1. Ingest Obsidian notes
2. Normalize them into typed memory objects
3. Detect entities, links, and themes
4. Create draft memories or summaries
5. Route them to a review queue
6. Approve or reject them explicitly
7. Promote only approved objects into the canonical memory layer
8. Feed approved project stories into JobPilot

## Trust and approval model

The second brain should have explicit states:

- draft
- reviewed
- approved
- archived
- rejected

A note can be imported without being trusted. A summary can be generated without being canonical. Only approved memory becomes part of the active second-brain substrate.

## MVP scope

The first useful slice should be intentionally narrow:

- ingest local Obsidian markdown notes,
- parse frontmatter and wikilinks,
- store them as typed notes with provenance,
- create a review queue for AI-generated summaries,
- support manual approval and rejection,
- expose approved memories to JobPilot as structured context.

## Integration with JobPilot

This is the important part: the second brain should not replace JobPilot. Instead:

- JobPilot continues to own career-focused workflows.
- The second brain provides the upstream memory substrate.
- Approved project stories and evidence from the second brain can be consumed by JobPilot for:
  - resume generation,
  - project narratives,
  - tailoring materials,
  - outreach drafts,
  - interview preparation.

That lets the repo support both personal knowledge management and career execution without mixing raw notes with canonical output.

## Implementation roadmap

### Phase 1 — foundation

- add a second-brain package under the repo,
- define note, memory, entity, and approval schemas,
- build a local Obsidian ingestion adapter,
- add a first-pass review workflow.

### Phase 2 — structure

- add entity recognition and memory linking,
- add note-to-memory normalization,
- add lightweight indexing for queries and retrieval.

### Phase 3 — AI assistance

- wire Claude-based summarization and extraction,
- keep all outputs draft-first,
- attach evidence to every generated claim.

### Phase 4 — JobPilot integration

- expose approved memories as project stories or career capsules,
- feed them into the existing review and rendering pipeline.

## Guardrails

- raw notes are never treated as truth,
- AI output is never auto-promoted,
- provenance is required for every structured memory,
- sensitive material stays opt-in and reviewable,
- all outputs should remain local-first when possible.

## Recommended next step

Start by implementing a local ingestion slice:

1. define the note/memory schema,
2. connect Obsidian markdown files as the first source,
3. create a review queue for imported notes,
4. add a simple approval endpoint or workflow,
5. then layer Claude summaries on top of that foundation.
