"""Master CV V2 snapshots: a version is a snapshot of APPROVED claims only.

The non-negotiable: nothing Claude-extracted becomes canonical without human review.
Enforcement lives at the version-snapshot query — the service queries
``list_claims(user_id, status=APPROVED)`` — and again here as a hard filter, so even
a buggy caller cannot leak an unapproved claim into a version.

The content shape is designed for the M11 renderer context builder: experiences
grouped by their user-assigned ``section`` (the two template groups), ordered by
``sort_order``, each carrying its approved claims with full evidence provenance.

Pure logic: building content and fingerprinting are deterministic; persistence sits
behind :class:`MasterCvSnapshotStore` (idempotent versioning, same philosophy as the
V1 ``MasterCvRepository``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.domain.claims import Claim, ClaimStatus, Experience, ExperienceSection
from app.domain.cv import MasterCv, ParClaim

SNAPSHOT_KIND = "approved_claims"

# ``ParClaim.source_type`` for claims adapted from an approved-claims snapshot: the
# provenance is the reviewed claim itself (``source_ref`` = ``claim:<id>``), not a
# raw evidence document.
SOURCE_APPROVED_CLAIM = "approved_claim"


def _claim_entry(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.id,
        "problem": claim.problem_text,
        "problem_cost_dimension": (
            claim.problem_cost_dimension.value if claim.problem_cost_dimension else None
        ),
        "problem_inefficiency": (
            claim.problem_inefficiency.value if claim.problem_inefficiency else None
        ),
        "action": claim.action_text,
        "action_tools": list(claim.action_tools),
        "result": claim.result_text,
        "result_kind": claim.result_kind.value,
        "result_status": claim.result_status.value,
        "result_metric_json": claim.result_metric_json,
        "evidence": [
            {
                "evidence_id": link.evidence_id,
                "field": link.field.value,
                "outcome_quote": link.outcome_quote,
            }
            for link in claim.evidence
        ],
    }


def build_snapshot_content(
    experiences: Sequence[Experience], claims: Sequence[Claim]
) -> dict[str, Any]:
    """Build a version's ``content_json`` from approved claims, grouped for rendering.

    Any claim that is not ``approved`` is dropped here regardless of what the caller
    passed — the belt to the service query's suspenders. Experiences with no approved
    claims do not appear (nothing to render).
    """
    approved = [c for c in claims if c.status is ClaimStatus.APPROVED]
    claims_by_experience: dict[int, list[Claim]] = {}
    for claim in approved:
        claims_by_experience.setdefault(claim.experience_id, []).append(claim)

    sections: dict[str, list[dict[str, Any]]] = {
        ExperienceSection.PROFESSIONAL_EXPERIENCE.value: [],
        ExperienceSection.PROJECTS_HACKATHONS.value: [],
    }
    for experience in sorted(experiences, key=lambda e: (e.sort_order, e.id)):
        experience_claims = claims_by_experience.get(experience.id)
        if not experience_claims:
            continue
        sections[experience.section.value].append(
            {
                "experience_id": experience.id,
                "name": experience.name,
                "subtitle": experience.subtitle,
                "dates": experience.dates,
                "sort_order": experience.sort_order,
                "claims": [_claim_entry(c) for c in sorted(experience_claims, key=lambda c: c.id)],
            }
        )
    return {"snapshot_of": SNAPSHOT_KIND, "sections": sections}


def snapshot_fingerprint(content: dict[str, Any]) -> str:
    """Stable hash of the substantive content (excluding the version stamp)."""
    material = {key: value for key, value in content.items() if key != "version"}
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredSnapshot:
    """One persisted Master CV version built from approved claims."""

    user_id: str
    version: int
    content: dict[str, Any]
    content_hash: str
    created_at: datetime | None = None


def master_cv_from_snapshot(snapshot: StoredSnapshot) -> MasterCv:
    """Adapt an approved-claims snapshot into the :class:`MasterCv` consumers expect.

    Matching, tailoring, and prep generation all take a ``MasterCv``; this is the only
    bridge from the reviewed V2 ledger to those consumers, so everything derived from
    the "Master CV" traces back to approved claims. Each ``ParClaim`` keeps provenance
    through ``source_ref = claim:<id>``.
    """
    claims: list[ParClaim] = []
    sections: dict[str, Any] = snapshot.content.get("sections", {})
    for entries in sections.values():
        for experience in entries:
            for entry in experience.get("claims", []):
                action = entry.get("action") or ""
                if not action:
                    continue  # an approved claim always carries an Action; skip malformed rows
                claims.append(
                    ParClaim(
                        action=action,
                        problem=entry.get("problem"),
                        result=entry.get("result"),
                        source_type=SOURCE_APPROVED_CLAIM,
                        source_ref=f"claim:{entry.get('claim_id')}",
                    )
                )
    return MasterCv(claims=claims, sources=[], version=snapshot.version)


@runtime_checkable
class MasterCvSnapshotStore(Protocol):
    """Versioned persistence for claim snapshots.

    ``save`` is **idempotent**: content whose fingerprint matches the latest version
    returns that version instead of creating a duplicate.
    """

    def save(self, user_id: str, content: dict[str, Any]) -> StoredSnapshot: ...

    def get_latest(self, user_id: str) -> StoredSnapshot | None: ...

    def get_version(self, user_id: str, version: int) -> StoredSnapshot | None: ...

    def list_versions(self, user_id: str) -> list[int]: ...
