# JobPilot

**A personalized, mostly-automated job-search pipeline that can prove every sentence it writes.**

One user, one system: it builds a canonical Master CV from the user's own artifacts, finds fresh high-fit roles, tailors materials, drafts targeted outreach, detects interview invitations, and generates prep packets — under a single non-negotiable invariant: *no prose leaves the machine unless it traces to evidence a human approved.* This document describes the project in the same Problem–Action–Result frame the system itself enforces.

---

## The business problem

**The job search itself is a nightmare, and it is an employer's market — because only one side is playing with AI.**

AI has raised the bar for technical ability while entry-level tech hiring declines, and employers have industrialized their side of the process: ATS filters, AI-screened applications, AI-conducted interviews, AI-written job postings. Job seekers, meanwhile, barely leverage AI in the search process itself — and not for lack of ability (the same people use AI successfully every day). The real bottlenecks are psychological: **discouragement, burnout, and hopelessness.** Applicants lose the willingness to keep pushing and the belief that an edge is even possible.

But the job market is literally a market, and the optimal strategy in any market is to identify every edge you have and relentlessly push your advantage. Markets are only unbeatable when all participants have perfect information and act rationally — and human behavior (2008, the 2020 pandemic, the GME squeeze, crypto panic-selling) shows they don't. An asymmetry this large — one side fully AI-leveraged, the other side burned out and under-tooled — is a solvable game-theory problem. What's missing is a **fully end-to-end job-hunting system optimized for the individual seeker** that attacks the real bottlenecks directly: it does the relentless part (tailoring every application to the posting, systemized networking, mining the seeker's own Drive/GitHub/transcripts into a master CV source of truth, interview prep on detection) so the human only has to do the judgment part — maximizing *fit*, not application volume.

Executing that thesis surfaces the secondary problems JobPilot had to solve:

- **Time.** Manually tailoring a resume and outreach message per application costs 30–60 minutes each; done honestly across dozens of postings, it is a part-time job. Done dishonestly (one generic blast), it converts at near zero — and feeds the burnout loop above.
- **Risk & quality.** The naive fix — "have an LLM write it" — produces polished, *unsupported* claims. This project's own first attempt proved it: an internal architecture audit (`docs/V2_AUDIT.md`) found the V1 pipeline had turned messy source documents into 133 unreviewed claims with one-word "problems" (`"manual"`, `"~$8M"`), resume headers classified as pain points, fragment actions (`"design,"`, `"extracted"`), the same metric attached to two different pieces of work — and that output was feeding the messages that actually left the machine. A fabricated metric in front of a hiring manager is not a bug; it is a credibility event.
- **Scattered, mangled evidence.** The truth about a person's work lives in Drive PDFs (word-per-line extraction damage), GitHub READMEs and commit logs, and loose uploads — across multiple resume versions of the same job and multi-project documents. No single artifact is canonical; assembling one by hand doesn't stay current.
- **Money.** Uncontrolled LLM usage burned a $20 API balance in roughly a day of extraction runs — client timeouts triggered retries that re-billed work the server had already completed, and re-runs re-paid full price for unchanged evidence. A tool meant to be sold as a product cannot bleed its users' API budgets.

## What JobPilot does (the actions)

### Evidence → verified claims (the claim ledger)

- **Ingests the user's real evidence, read-only,** through MCP servers for Google Drive (scoped to an approved career-docs folder) and GitHub (own repos: READMEs + commit messages), plus a local uploads directory — behind narrow interfaces with mock-first implementations, so the entire pipeline runs offline on fixtures with zero credentials.
- **Repairs reality before reading it:** deterministic source normalization reflows word-per-line PDF damage, de-hyphenates line breaks, and preserves bullets/labels/headers, so downstream extraction sees prose, not shrapnel.
- **Discovers the project roster** — the real-world entities (employer roles, projects) a resume is organized under — via an LLM proposal over all sources, then **requires human confirmation** (merge duplicates, rename, discard junk) before anything is scoped. Live run: 119 documents → 9 proposed entities with correct dates (4 employers, 5 projects), confirmed alongside 6 repo projects; 741 evidence chunks assigned to entities with exact character spans, 4 honestly left unassigned.
- **Extracts Problem–Action–Result claims** with a two-pass LLM extractor (work statements, then outcome statements), where every quote is verified verbatim against its cited chunk and ungrounded output is dropped — plus a deterministic heuristic extractor as the offline default.
- **Gates every claim** through a deterministic PAR validator: problems must declare a cost dimension or inefficiency; actions must name tools that appear in the text; quantified results require the metric verbatim in cited evidence; missing results are recorded as missing, never filled. Structurally unreviewable output (fragments, header-shaped "problems", cross-project citations, reused outcome spans) is dropped before persistence, not queued.
- **Puts a human between extraction and canon:** claims land `pending_review`; approval, edit-with-attestation (typed statements become provenance-carrying evidence rows), or rejection-with-reason — and rejections are retained forever as labeled data.

### Verified claims → everything downstream

- **The Master CV is exclusively a snapshot of approved claims,** versioned and fingerprint-idempotent, rendered to docx through a frozen template cloned from the real CV. Header, education, and skills come from a profile file — never invented.
- **Matching:** nightly two-stage ranking of fresh postings (cheap scoring over all, deep re-rank of the top N with rationale), deduped and idempotent.
- **Tailoring & outreach:** materials are assembled from approved claims only; every highlight carries the ID of the claim behind it, and a number-factuality gate fails any LLM draft containing a number found in neither the referenced claims nor the posting — invented metrics cannot reach the approval queue, let alone an email. Outreach sends only after explicit approval, only to researched contacts (never guessed addresses), through a state machine that cannot re-send.
- **Interview handling:** a scoped, query-filtered inbox scan detects invitations, re-fetches each message by ID, and verifies the quoted evidence is a verbatim substring of the real body before any record exists; confirmed interviews get generated prep packets.
- **Self-measurement:** every extraction run records a slop-metrics scorecard (fragment rate, duplicate rate, missing-result rate, flag rate, cross-project links — hard-fail if nonzero) in an auditable validation log, and every human review decision accrues into a golden set exportable as labeled JSONL.
- **Cost controls:** oversized evidence groups split into bounded batches; shared context rides in the prompt cache (later passes at ~10% input price); unchanged evidence groups skip extraction entirely via content fingerprints; model tiers are env-configured (bulk vs. deep); per-call token costs are logged.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic (11 migrations) · Anthropic Messages API (tiered models, strict-JSON with retry-once repair) · MCP client sessions (Drive, GitHub) · APScheduler (Lambda-portable job bodies) · Next.js + Tailwind dashboard · pytest/ruff/mypy, 629 tests, mock-first throughout.

## The results

All measured, from the repository's own audit trail:

- **Slop made unrepresentable, verified on live data.** Audit baseline: 89 of 104 claims flagged, with one-word problems, fragment actions, duplicated bullets, and cross-project results. Current live queue after the four-phase remediation: **148 claims — 0 fragment actions, 0 unspecific problems, 0 duplicates, 0 cross-project evidence links** (`python -m app.tools.eval_extraction`, recorded per run).
- **Real headings from messy evidence.** The same unedited source set (4 near-duplicate resume PDFs, a degree form, one multi-project case-study PDF, 6 repos) now yields claims scoped under *"Wellington Management — Data Architecture & Engineering Intern (Jun–Aug 2025)"* instead of `cmorreale_resume.docx (1).pdf` — without curating the source folder, which was an explicit requirement.
- **Truthfulness is structural, not aspirational.** Cross-project results and reused outcome spans are impossible by construction (per-entity extraction groups); unsupported numbers in generated prose fail closed to verbatim rendering; nothing renders or sends without a human-approved claim behind it — proven end-to-end by the exit tests and a full fixture loop.
- **~3× cheaper fresh extractions, $0 unchanged re-runs.** Root-caused a triple cost leak observed live (60s client timeout re-billing completed work up to 3×; futile re-extraction doubling every commit-heavy group; full re-runs re-paying for unchanged evidence) and eliminated all three; a no-change re-run now costs nothing.
- **Failure is loud and isolated.** LLM failures skip their group with a logged, recorded failure — never silently answered by a weaker extractor (the silent-fallback pattern that caused mixed-quality output was deleted); one job's failure never blocks another; API timeouts on oversized repos were eliminated by batching (verified on the repo that previously failed every run).
- **Regression-proof against reality.** A production-damage fixture (contact headers, word-per-line runs, date-range job headers) must score zero on every audited failure mode in CI; the evaluation harness runs offline at zero LLM cost.

**Honest status:** extraction, roster, validation, rendering, and the evaluation harness are live-verified against the user's real Drive/GitHub; matching, tailoring, outreach, and interview flows are verified end-to-end on fixtures and mock transports. The known next problem is documented in `docs/REVIEW_LAYER_AUDIT.md`: the review queue currently presents 148 claim-level decisions that should be ~12 project-level ones, and the project-completeness bar (every rendered project must carry a full P-A-R) is designed but not yet enforced.
