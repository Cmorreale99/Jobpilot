"""The evidence inventory: truthful, derived views over stored evidence (pure).

MASTER CV REPAIR §4.12/§5.4/§8/§10.3: every relevant evidence item survives ingestion
independently of claim or story readiness, and stays queryable and reviewable. This
module derives the inventory's two labels WITHOUT guessing:

* **Categories** (§5.4.6) come only from facts: a chunk cited by a claim's
  problem/action/result link carries that field; a user attestation is one; an uncited
  commit is supporting implementation; an uncited assigned chunk is context; an
  unassigned chunk is unresolved. No text classifier, no machine interpretation.
* **Source importance** (§6.3 default ranking) comes from the source type and path:
  explicit project docs and user-authored artifacts strongest, architecture/design
  docs next, PR/issue narratives next, commits supporting detail. Importance is kept
  separate from source COUNT (§4.4): reporting weighs signal, volume never wins.

Pure logic: no I/O, no repository imports beyond the domain shapes.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_DOC,
    SOURCE_GITHUB_PR,
    SOURCE_GITHUB_README,
    SOURCE_UPLOAD,
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimField,
    StoredEvidence,
)
from app.domain.repo_docs import is_claude_md, is_readme_path

# Category values (documented, not a CHECK constraint — same policy as source types).
CATEGORY_PROBLEM = "problem"
CATEGORY_ACTION = "action"
CATEGORY_RESULT = "result"
CATEGORY_USER_ATTESTATION = "user_attestation"
CATEGORY_SUPPORTING_IMPLEMENTATION = "supporting_implementation"
CATEGORY_CONTEXT = "context"
CATEGORY_UNRESOLVED = "unresolved"

# §6.3 default importance tiers (1 strongest). The user may override the ranking
# (§6.3 last line) — these are the binding defaults, not a hardcoded law.
TIER_PROJECT_DOC = 1
TIER_ARCHITECTURE_DOC = 2
TIER_REPORT_OR_PR = 3
TIER_CODE = 4
TIER_COMMIT = 5


def source_importance(source_type: str, source_ref: str = "") -> int:
    """The §6.3 default importance tier for one evidence source."""
    if source_type in (SOURCE_DRIVE, SOURCE_UPLOAD, SOURCE_GITHUB_README, SOURCE_USER_ATTESTATION):
        return TIER_PROJECT_DOC
    if source_type == SOURCE_GITHUB_DOC:
        # Nested READMEs and CLAUDE.md are explicit project docs; other repo Markdown
        # (docs/ARCHITECTURE.md, design notes) carries system-design signal.
        return (
            TIER_PROJECT_DOC
            if is_readme_path(source_ref) or is_claude_md(source_ref)
            else TIER_ARCHITECTURE_DOC
        )
    if source_type == SOURCE_GITHUB_PR:
        return TIER_REPORT_OR_PR
    if source_type == SOURCE_GITHUB_COMMIT:
        return TIER_COMMIT
    return TIER_ARCHITECTURE_DOC  # unknown-but-admitted sources sit mid-tier, visibly


def evidence_categories(row: StoredEvidence, claims: Sequence[Claim]) -> tuple[str, ...]:
    """Derived (never guessed) categories for one evidence row (§5.4.6).

    Claim citations are the strongest fact: a chunk cited under ``result`` IS result
    evidence, whatever else it is. Uncited rows fall back to source-type facts;
    unassigned rows are honestly unresolved.
    """
    cited: list[str] = []
    for claim in claims:
        for link in claim.evidence:
            if link.evidence_id != row.id:
                continue
            if link.field is ClaimField.PROBLEM and CATEGORY_PROBLEM not in cited:
                cited.append(CATEGORY_PROBLEM)
            elif link.field is ClaimField.ACTION and CATEGORY_ACTION not in cited:
                cited.append(CATEGORY_ACTION)
            elif link.field is ClaimField.RESULT and CATEGORY_RESULT not in cited:
                cited.append(CATEGORY_RESULT)
    if row.source_type == SOURCE_USER_ATTESTATION:
        return (CATEGORY_USER_ATTESTATION, *cited)
    if cited:
        return tuple(cited)
    if row.experience_id is None:
        return (CATEGORY_UNRESOLVED,)
    if row.source_type == SOURCE_GITHUB_COMMIT:
        return (CATEGORY_SUPPORTING_IMPLEMENTATION,)
    return (CATEGORY_CONTEXT,)


__all__ = [
    "CATEGORY_ACTION",
    "CATEGORY_CONTEXT",
    "CATEGORY_PROBLEM",
    "CATEGORY_RESULT",
    "CATEGORY_SUPPORTING_IMPLEMENTATION",
    "CATEGORY_UNRESOLVED",
    "CATEGORY_USER_ATTESTATION",
    "TIER_ARCHITECTURE_DOC",
    "TIER_CODE",
    "TIER_COMMIT",
    "TIER_PROJECT_DOC",
    "TIER_REPORT_OR_PR",
    "evidence_categories",
    "source_importance",
]
