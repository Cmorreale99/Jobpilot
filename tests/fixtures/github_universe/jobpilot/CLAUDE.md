# CLAUDE.md

Operating manual for anyone working in this repo.

JobPilot's non-negotiable invariant: every consequential claim traces to exact source
evidence or explicit user attestation. The review boundary is human confirmation — no
machine assignment is canonical until the user confirms it. Postgres is the transactional
source of truth; raw source text is captured before any lossy transformation.
