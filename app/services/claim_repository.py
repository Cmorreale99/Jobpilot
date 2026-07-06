"""In-memory :class:`ClaimRepository` — the mock-first default for dev and tests.

Implements the shared write semantics documented on the protocol
(``app/domain/claims.py``): experience dedupe on ``(user_id, name)``, evidence dedupe
on ``(user_id, source_type, source_ref)``, and the idempotency rule that re-extraction
replaces only ``extracted``/``pending_review`` claims — a human decision (approved or
rejected) is never deleted, and a re-extracted claim whose content matches a reviewed
one is never re-queued.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from app.domain.applications import validate_transition
from app.domain.claims import (
    EXPERIENCE_TRANSITIONS,
    SOURCE_USER_ATTESTATION,
    Claim,
    ClaimEditPlan,
    ClaimEvidenceLink,
    ClaimRepository,
    ClaimStatus,
    EvidenceChunk,
    Experience,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    StorableClaim,
    StoredEvidence,
    claim_content_fingerprint,
    validate_claim_transition,
)
from app.domain.text_normalization import NORMALIZATION_VERSION


class InMemoryClaimRepository(ClaimRepository):
    """Dict-backed claims persistence with the same semantics as the SQL version."""

    def __init__(self) -> None:
        self._experiences: dict[int, Experience] = {}
        self._evidence: dict[int, StoredEvidence] = {}
        self._claims: dict[int, Claim] = {}
        self._next_experience_id = 1
        self._next_evidence_id = 1
        self._next_claim_id = 1

    # --- experiences ---------------------------------------------------------------

    def upsert_experience(self, user_id: str, seed: ExperienceSeed) -> Experience:
        return self._upsert(user_id, seed, ExperienceStatus.CONFIRMED)

    def propose_experience(self, user_id: str, seed: ExperienceSeed) -> Experience:
        return self._upsert(user_id, seed, ExperienceStatus.PROPOSED)

    def _upsert(self, user_id: str, seed: ExperienceSeed, status: ExperienceStatus) -> Experience:
        for experience in self._experiences.values():
            if experience.user_id != user_id:
                continue
            # Existing row (matched by name OR alias, both directions) wins: its
            # status, section, and sort_order are human decisions — never overridden.
            if experience.matches_name(seed.name) or any(
                experience.matches_name(alias) for alias in seed.aliases
            ):
                return experience
        experience = Experience(
            id=self._next_experience_id,
            user_id=user_id,
            name=seed.name,
            section=seed.section,
            subtitle=seed.subtitle,
            dates=seed.dates,
            sort_order=sum(1 for e in self._experiences.values() if e.user_id == user_id),
            kind=seed.kind,
            status=status,
            aliases=seed.aliases,
        )
        self._experiences[experience.id] = experience
        self._next_experience_id += 1
        return experience

    def list_experiences(self, user_id: str) -> list[Experience]:
        rows = [e for e in self._experiences.values() if e.user_id == user_id]
        return sorted(rows, key=lambda e: (e.sort_order, e.id))

    def set_experience_status(self, experience_id: int, status: ExperienceStatus) -> Experience:
        experience = self._experiences.get(experience_id)
        if experience is None:
            raise LookupError(f"no experience with id {experience_id}")
        validate_transition(EXPERIENCE_TRANSITIONS, experience.status, status)
        updated = replace(experience, status=status)
        self._experiences[experience_id] = updated
        return updated

    def update_experience_details(
        self,
        experience_id: int,
        *,
        name: str | None = None,
        subtitle: str | None = None,
        dates: str | None = None,
        kind: ExperienceKind | None = None,
        aliases: tuple[str, ...] | None = None,
    ) -> Experience:
        experience = self._experiences.get(experience_id)
        if experience is None:
            raise LookupError(f"no experience with id {experience_id}")
        if name is not None and name.strip() and name != experience.name:
            # A rename keeps the old name recognizable to future detections.
            merged_aliases = tuple(dict.fromkeys((*experience.aliases, experience.name)))
            experience = replace(experience, name=name.strip(), aliases=merged_aliases)
        if subtitle is not None:
            experience = replace(experience, subtitle=subtitle or None)
        if dates is not None:
            experience = replace(experience, dates=dates or None)
        if kind is not None:
            experience = replace(experience, kind=kind)
        if aliases is not None:
            experience = replace(experience, aliases=tuple(dict.fromkeys(aliases)))
        self._experiences[experience_id] = experience
        return experience

    def merge_experiences(self, source_id: int, target_id: int) -> Experience:
        source = self._experiences.get(source_id)
        target = self._experiences.get(target_id)
        if source is None or target is None:
            raise LookupError(f"no experience with id {source_id if source is None else target_id}")
        if source_id == target_id:
            raise ValueError("cannot merge an experience into itself")
        if source.status is ExperienceStatus.MERGED or source.status is ExperienceStatus.DISCARDED:
            raise ValueError(f"experience {source_id} is {source.status} and cannot be merged")
        if target.status is not ExperienceStatus.CONFIRMED:
            raise ValueError("merge target must be a confirmed experience")
        for claim_id, claim in self._claims.items():
            if claim.experience_id == source_id:
                self._claims[claim_id] = replace(claim, experience_id=target_id)
        for evidence_id, stored in self._evidence.items():
            if stored.experience_id == source_id:
                self._evidence[evidence_id] = replace(stored, experience_id=target_id)
        merged_aliases = tuple(dict.fromkeys((*target.aliases, source.name, *source.aliases)))
        target = replace(target, aliases=merged_aliases)
        self._experiences[target_id] = target
        self._experiences[source_id] = replace(
            source, status=ExperienceStatus.MERGED, merged_into_id=target_id
        )
        return target

    def update_experience_layout(
        self,
        experience_id: int,
        *,
        section: ExperienceSection | None = None,
        sort_order: int | None = None,
    ) -> Experience:
        experience = self._experiences.get(experience_id)
        if experience is None:
            raise LookupError(f"no experience with id {experience_id}")
        if section is not None:
            experience = replace(experience, section=section)
        if sort_order is not None:
            experience = replace(experience, sort_order=sort_order)
        self._experiences[experience_id] = experience
        return experience

    def set_extraction_hash(self, experience_id: int, value: str | None) -> Experience:
        experience = self._experiences.get(experience_id)
        if experience is None:
            raise LookupError(f"no experience with id {experience_id}")
        updated = replace(experience, extraction_hash=value)
        self._experiences[experience_id] = updated
        return updated

    # --- evidence --------------------------------------------------------------------

    def upsert_evidence(self, user_id: str, chunk: EvidenceChunk) -> StoredEvidence:
        for stored in self._evidence.values():
            if (
                stored.user_id == user_id
                and stored.source_type == chunk.source_type
                and stored.source_ref == chunk.source_ref
            ):
                if stored.chunk_text != chunk.chunk_text:
                    # Fresh text = fresh normalizer output: re-stamp the generation.
                    stored = replace(
                        stored,
                        chunk_text=chunk.chunk_text,
                        normalization_version=NORMALIZATION_VERSION,
                    )
                    self._evidence[stored.id] = stored
                return stored
        stored = StoredEvidence(
            id=self._next_evidence_id,
            user_id=user_id,
            source_type=chunk.source_type,
            source_ref=chunk.source_ref,
            chunk_text=chunk.chunk_text,
            created_at=datetime.now(tz=UTC),
            normalization_version=NORMALIZATION_VERSION,
        )
        self._evidence[stored.id] = stored
        self._next_evidence_id += 1
        return stored

    def get_evidence(self, evidence_id: int) -> StoredEvidence | None:
        return self._evidence.get(evidence_id)

    def get_evidence_by_ref(
        self, user_id: str, source_type: str, source_ref: str
    ) -> StoredEvidence | None:
        return next(
            (
                e
                for e in self._evidence.values()
                if e.user_id == user_id
                and e.source_type == source_type
                and e.source_ref == source_ref
            ),
            None,
        )

    def assign_evidence(self, evidence_id: int, experience_id: int | None) -> StoredEvidence:
        stored = self._evidence.get(evidence_id)
        if stored is None:
            raise LookupError(f"no evidence with id {evidence_id}")
        updated = replace(stored, experience_id=experience_id)
        self._evidence[evidence_id] = updated
        return updated

    def list_assigned_evidence(self, user_id: str, experience_id: int) -> list[StoredEvidence]:
        rows = [
            e
            for e in self._evidence.values()
            if e.user_id == user_id and e.experience_id == experience_id
        ]
        return sorted(rows, key=lambda e: e.id)

    def list_unassigned_evidence(self, user_id: str) -> list[StoredEvidence]:
        # User attestations (claim:/story: refs) are human answers, never source
        # chunks awaiting roster assignment — they must not surface in this queue.
        rows = [
            e
            for e in self._evidence.values()
            if e.user_id == user_id
            and e.experience_id is None
            and e.source_type != SOURCE_USER_ATTESTATION
        ]
        return sorted(rows, key=lambda e: e.id)

    # --- claims ----------------------------------------------------------------------

    def replace_unreviewed_claims(
        self, user_id: str, experience_id: int, claims: Sequence[StorableClaim]
    ) -> list[Claim]:
        reviewed_fingerprints = set()
        for claim in list(self._claims.values()):
            if claim.user_id != user_id or claim.experience_id != experience_id:
                continue
            if claim.status in (ClaimStatus.EXTRACTED, ClaimStatus.PENDING_REVIEW):
                del self._claims[claim.id]
            else:
                reviewed_fingerprints.add(
                    claim_content_fingerprint(
                        claim.problem_text, claim.action_text, claim.result_text
                    )
                )

        inserted: list[Claim] = []
        for storable in claims:
            draft = storable.draft
            fingerprint = claim_content_fingerprint(
                draft.problem_text, draft.action_text, draft.result_text
            )
            if fingerprint in reviewed_fingerprints:
                continue  # a human already decided on this content; never re-queue it
            links = tuple(
                ClaimEvidenceLink(
                    evidence_id=self.upsert_evidence(user_id, ref.chunk).id,
                    field=ref.field,
                    outcome_quote=ref.outcome_quote,
                )
                for ref in draft.evidence
            )
            claim = Claim(
                id=self._next_claim_id,
                user_id=user_id,
                experience_id=experience_id,
                status=storable.status,
                action_text=draft.action_text,
                action_tools=draft.action_tools,
                problem_text=draft.problem_text,
                problem_cost_dimension=draft.problem_cost_dimension,
                problem_inefficiency=draft.problem_inefficiency,
                result_text=draft.result_text,
                result_kind=draft.result_kind,
                result_status=storable.result_status,
                result_metric_json=draft.result_metric_json,
                validation_flags=storable.validation_flags,
                evidence=links,
            )
            self._claims[claim.id] = claim
            self._next_claim_id += 1
            inserted.append(claim)
        return inserted

    def get_claim(self, claim_id: int) -> Claim | None:
        return self._claims.get(claim_id)

    def list_claims(self, user_id: str, status: ClaimStatus | None = None) -> list[Claim]:
        rows = [
            c
            for c in self._claims.values()
            if c.user_id == user_id and (status is None or c.status is status)
        ]
        return sorted(rows, key=lambda c: c.id)

    def apply_claim_edit(self, user_id: str, plan: ClaimEditPlan) -> Claim:
        claim = self._claims.get(plan.claim_id)
        if claim is None:
            raise LookupError(f"no claim with id {plan.claim_id}")
        links = list(claim.evidence)
        for attestation in plan.attestations:
            evidence = self.upsert_evidence(user_id, attestation.chunk)
            link = ClaimEvidenceLink(
                evidence_id=evidence.id,
                field=attestation.field,
                outcome_quote=attestation.outcome_quote,
            )
            # Replace an existing link to the same attestation row (re-edit),
            # keep links to original source evidence untouched.
            links = [
                existing
                for existing in links
                if not (existing.evidence_id == evidence.id and existing.field == link.field)
            ]
            links.append(link)
        updated = replace(claim, evidence=tuple(links), **plan.updates)
        self._claims[claim.id] = updated
        return updated

    def transition_claim(
        self,
        claim_id: int,
        new_status: ClaimStatus,
        *,
        review_note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> Claim:
        claim = self._claims.get(claim_id)
        if claim is None:
            raise LookupError(f"no claim with id {claim_id}")
        validate_claim_transition(claim.status, new_status, review_note=review_note)
        updated = replace(
            claim,
            status=new_status,
            review_note=review_note if review_note is not None else claim.review_note,
            reviewed_at=reviewed_at or datetime.now(tz=UTC),
        )
        self._claims[claim_id] = updated
        return updated
