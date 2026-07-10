"""Cooper.ai fixture — real-shaped resume evidence spanning multiple problem spaces.

The live-corpus ``Cooper.ai`` spec is synthetic (``topic="onboarding"``, 0 results) and
unusable for the v3.1 result-extraction and problem-space-separation tests. This module
holds the real FedEx / Pacifica / dataset-delivery text: three distinct problem spaces,
each with its own Problem, Action statements (leading with work verbs), and Result
statements (the defensible outcomes the old classifier dropped — non-percentage coverage,
reliability, automation, operational, and delivery results, plus quantified ones).

Reused by Increment 1 (result extraction) and Increment 2 (problem-space separation).
Statements are deliberately one-per-line so classification is unambiguous, and every
Result phrase is a verbatim substring of its chunk (the grounding checks depend on it).
"""

from __future__ import annotations

from app.domain.claims import (
    SOURCE_DRIVE,
    Claim,
    ClaimEvidenceLink,
    ClaimStatus,
    EvidenceChunk,
    EvidenceGroup,
    ExperienceSection,
    ExperienceSeed,
    HeuristicTwoPassExtractor,
)

# --- Problem space A: FedEx shipping data integrity (quantified + coverage results) ---
FEDEX_TEXT = (
    "FedEx shipping data integrity\n"
    "Problem: A FedEx carrier schema migration caused duplicate records, date coverage "
    "gaps, and overstated charge reporting.\n"
    "Rebuilt the FedEx shipping fact model after the carrier schema migration.\n"
    "Reworked carrier-specific Snowflake refresh logic.\n"
    "Instrumented data-quality checks across the FedEx shipping tables.\n"
    "Removed 195K+ duplicate FedEx records.\n"
    "Restored 100% date coverage.\n"
    "Corrected overstated charge reporting from $4.01M to $2.16M."
)

# --- Problem space B: Pacifica recurring ingestion automation (automation/operational) -
PACIFICA_TEXT = (
    "Pacifica recurring ingestion automation\n"
    "Problem: Recurring client logistics reporting required manual file preparation and "
    "warehouse refreshes.\n"
    "Built an event-driven AWS Lambda to S3 to Snowflake ingestion pipeline.\n"
    "Automated recurring client data-ingestion workflows.\n"
    "Eliminated recurring manual ETL effort.\n"
    "Enabled daily warehouse refreshes."
)

# --- Problem space C: production dataset delivery (delivery + analytics results) -------
DATASET_TEXT = (
    "Production dataset delivery\n"
    "Problem: Client analytics and AI workflows needed reliable production Snowflake "
    "datasets.\n"
    "Built Snowpark ingestion with validation controls and fail-safe loading patterns.\n"
    "Structured Snowpark validation controls for the client datasets.\n"
    "Delivered five production Snowflake datasets.\n"
    "Enabled analytics and AI applications."
)

FEDEX_CHUNK = EvidenceChunk(SOURCE_DRIVE, "cooper_fedex", FEDEX_TEXT)
PACIFICA_CHUNK = EvidenceChunk(SOURCE_DRIVE, "cooper_pacifica", PACIFICA_TEXT)
DATASET_CHUNK = EvidenceChunk(SOURCE_DRIVE, "cooper_datasets", DATASET_TEXT)

# The defensible Results that the pre-v3.1 heuristic dropped or misread as work.
FEDEX_RESULTS = (
    "Removed 195K+ duplicate FedEx records.",
    "Restored 100% date coverage.",
    "Corrected overstated charge reporting from $4.01M to $2.16M.",
)
PACIFICA_RESULTS = (
    "Eliminated recurring manual ETL effort.",
    "Enabled daily warehouse refreshes.",
)
DATASET_RESULTS = (
    "Delivered five production Snowflake datasets.",
    "Enabled analytics and AI applications.",
)
ALL_RESULT_STATEMENTS = FEDEX_RESULTS + PACIFICA_RESULTS + DATASET_RESULTS


def cooper_group() -> EvidenceGroup:
    """The full Cooper.ai evidence group (all three problem-space chunks)."""
    return EvidenceGroup(
        experience=ExperienceSeed(name="Cooper.ai", section=ExperienceSection.PROJECTS_HACKATHONS),
        chunks=(FEDEX_CHUNK, PACIFICA_CHUNK, DATASET_CHUNK),
    )


def claims_from_group(
    group: EvidenceGroup, *, experience_id: int, user_id: str = "u1"
) -> list[Claim]:
    """Extract a group heuristically and shape the drafts as persisted claims.

    Evidence ids are assigned per ``source_ref`` (mirroring ``upsert_evidence``'s
    dedupe key) so co-citation is representable; claim ids follow extraction order.
    All claims land ``pending_review`` — problem-space detection only excludes
    ``rejected``.
    """
    drafts = HeuristicTwoPassExtractor().extract(group)
    evidence_ids: dict[str, int] = {}
    claims: list[Claim] = []
    for claim_id, draft in enumerate(drafts, start=1):
        links = tuple(
            ClaimEvidenceLink(
                evidence_id=evidence_ids.setdefault(ref.chunk.source_ref, 100 + len(evidence_ids)),
                field=ref.field,
                outcome_quote=ref.outcome_quote,
            )
            for ref in draft.evidence
        )
        claims.append(
            Claim(
                id=claim_id,
                user_id=user_id,
                experience_id=experience_id,
                status=ClaimStatus.PENDING_REVIEW,
                action_text=draft.action_text,
                action_tools=draft.action_tools,
                problem_text=draft.problem_text,
                problem_cost_dimension=draft.problem_cost_dimension,
                problem_inefficiency=draft.problem_inefficiency,
                result_text=draft.result_text,
                result_kind=draft.result_kind,
                result_metric_json=draft.result_metric_json,
                evidence=links,
            )
        )
    return claims


def cooper_claims(experience_id: int = 1, user_id: str = "u1") -> list[Claim]:
    """The Cooper.ai group as persisted-shaped claims (see :func:`claims_from_group`)."""
    return claims_from_group(cooper_group(), experience_id=experience_id, user_id=user_id)
