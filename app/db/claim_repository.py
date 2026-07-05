"""SQLAlchemy-backed :class:`ClaimRepository`.

Same write semantics as the in-memory implementation (the protocol documents them):
experience dedupe on ``(user_id, name)`` with review-owned fields never overwritten,
evidence dedupe on ``(user_id, source_type, source_ref)``, and re-extraction that
replaces only unreviewed claims — approved/rejected rows are retained forever and
matching content is never re-queued.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ClaimEvidenceRow, ClaimRow, EvidenceRow, ExperienceRow
from app.domain.applications import validate_transition
from app.domain.claims import (
    EXPERIENCE_TRANSITIONS,
    Claim,
    ClaimEditPlan,
    ClaimEvidenceLink,
    ClaimField,
    ClaimStatus,
    CostDimension,
    EvidenceChunk,
    Experience,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    Inefficiency,
    ResultKind,
    ResultStatus,
    StorableClaim,
    StoredEvidence,
    claim_content_fingerprint,
    validate_claim_transition,
)

_UNREVIEWED = (ClaimStatus.EXTRACTED.value, ClaimStatus.PENDING_REVIEW.value)


def _to_column_value(value: Any) -> Any:
    """Convert a domain edit value to its DB representation (enums/tuples -> str/list)."""
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return list(value)
    return value


def _to_experience(row: ExperienceRow) -> Experience:
    return Experience(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        section=ExperienceSection(row.section),
        subtitle=row.subtitle,
        dates=row.dates,
        sort_order=row.sort_order,
        kind=ExperienceKind(row.kind),
        status=ExperienceStatus(row.status),
        aliases=tuple(row.aliases or []),
        merged_into_id=row.merged_into_id,
        extraction_hash=row.extraction_hash,
    )


def _to_evidence(row: EvidenceRow) -> StoredEvidence:
    return StoredEvidence(
        id=row.id,
        user_id=row.user_id,
        source_type=row.source_type,
        source_ref=row.source_ref,
        chunk_text=row.chunk_text,
        created_at=row.created_at,
        experience_id=row.experience_id,
    )


def _to_claim(row: ClaimRow, links: Sequence[ClaimEvidenceRow]) -> Claim:
    return Claim(
        id=row.id,
        user_id=row.user_id,
        experience_id=row.experience_id,
        status=ClaimStatus(row.status),
        action_text=row.action_text,
        action_tools=tuple(row.action_tools or []),
        problem_text=row.problem_text,
        problem_cost_dimension=(
            CostDimension(row.problem_cost_dimension) if row.problem_cost_dimension else None
        ),
        problem_inefficiency=(
            Inefficiency(row.problem_inefficiency) if row.problem_inefficiency else None
        ),
        result_text=row.result_text,
        result_kind=ResultKind(row.result_kind),
        result_status=ResultStatus(row.result_status),
        result_metric_json=row.result_metric_json,
        validation_flags=tuple(row.validation_flags or []),
        review_note=row.review_note,
        reviewed_at=row.reviewed_at,
        evidence=tuple(
            ClaimEvidenceLink(
                evidence_id=link.evidence_id,
                field=ClaimField(link.field),
                outcome_quote=link.outcome_quote,
            )
            for link in links
        ),
    )


class SqlClaimRepository:
    """Claims persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # --- experiences ---------------------------------------------------------------

    def upsert_experience(self, user_id: str, seed: ExperienceSeed) -> Experience:
        return self._upsert(user_id, seed, ExperienceStatus.CONFIRMED)

    def propose_experience(self, user_id: str, seed: ExperienceSeed) -> Experience:
        return self._upsert(user_id, seed, ExperienceStatus.PROPOSED)

    def _upsert(self, user_id: str, seed: ExperienceSeed, status: ExperienceStatus) -> Experience:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExperienceRow).where(ExperienceRow.user_id == user_id)
            ).all()
            for row in rows:
                # Existing row (matched by name OR alias, both directions) wins: its
                # status, section, and sort_order are human decisions.
                existing = _to_experience(row)
                if existing.matches_name(seed.name) or any(
                    existing.matches_name(alias) for alias in seed.aliases
                ):
                    return existing
            row = ExperienceRow(
                user_id=user_id,
                name=seed.name,
                subtitle=seed.subtitle,
                dates=seed.dates,
                section=seed.section.value,
                sort_order=len(rows),
                kind=seed.kind.value,
                status=status.value,
                aliases=list(seed.aliases),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_experience(row)

    def set_experience_status(self, experience_id: int, status: ExperienceStatus) -> Experience:
        with self._session_factory() as session:
            row = session.get(ExperienceRow, experience_id)
            if row is None:
                raise LookupError(f"no experience with id {experience_id}")
            validate_transition(EXPERIENCE_TRANSITIONS, ExperienceStatus(row.status), status)
            row.status = status.value
            session.commit()
            session.refresh(row)
            return _to_experience(row)

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
        with self._session_factory() as session:
            row = session.get(ExperienceRow, experience_id)
            if row is None:
                raise LookupError(f"no experience with id {experience_id}")
            if name is not None and name.strip() and name != row.name:
                # A rename keeps the old name recognizable to future detections.
                row.aliases = list(dict.fromkeys([*(row.aliases or []), row.name]))
                row.name = name.strip()
            if subtitle is not None:
                row.subtitle = subtitle or None
            if dates is not None:
                row.dates = dates or None
            if kind is not None:
                row.kind = kind.value
            if aliases is not None:
                row.aliases = list(dict.fromkeys(aliases))
            session.commit()
            session.refresh(row)
            return _to_experience(row)

    def merge_experiences(self, source_id: int, target_id: int) -> Experience:
        with self._session_factory() as session:
            source = session.get(ExperienceRow, source_id)
            target = session.get(ExperienceRow, target_id)
            if source is None or target is None:
                missing = source_id if source is None else target_id
                raise LookupError(f"no experience with id {missing}")
            if source_id == target_id:
                raise ValueError("cannot merge an experience into itself")
            source_status = ExperienceStatus(source.status)
            if source_status in (ExperienceStatus.MERGED, ExperienceStatus.DISCARDED):
                raise ValueError(f"experience {source_id} is {source.status} and cannot be merged")
            if ExperienceStatus(target.status) is not ExperienceStatus.CONFIRMED:
                raise ValueError("merge target must be a confirmed experience")
            for claim_row in session.scalars(
                select(ClaimRow).where(ClaimRow.experience_id == source_id)
            ):
                claim_row.experience_id = target_id
            for evidence_row in session.scalars(
                select(EvidenceRow).where(EvidenceRow.experience_id == source_id)
            ):
                evidence_row.experience_id = target_id
            target.aliases = list(
                dict.fromkeys([*(target.aliases or []), source.name, *(source.aliases or [])])
            )
            source.status = ExperienceStatus.MERGED.value
            source.merged_into_id = target_id
            session.commit()
            session.refresh(target)
            return _to_experience(target)

    def list_experiences(self, user_id: str) -> list[Experience]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExperienceRow)
                .where(ExperienceRow.user_id == user_id)
                .order_by(ExperienceRow.sort_order, ExperienceRow.id)
            )
            return [_to_experience(row) for row in rows]

    def update_experience_layout(
        self,
        experience_id: int,
        *,
        section: ExperienceSection | None = None,
        sort_order: int | None = None,
    ) -> Experience:
        with self._session_factory() as session:
            row = session.get(ExperienceRow, experience_id)
            if row is None:
                raise LookupError(f"no experience with id {experience_id}")
            if section is not None:
                row.section = section.value
            if sort_order is not None:
                row.sort_order = sort_order
            session.commit()
            session.refresh(row)
            return _to_experience(row)

    def set_extraction_hash(self, experience_id: int, value: str | None) -> Experience:
        with self._session_factory() as session:
            row = session.get(ExperienceRow, experience_id)
            if row is None:
                raise LookupError(f"no experience with id {experience_id}")
            row.extraction_hash = value
            session.commit()
            session.refresh(row)
            return _to_experience(row)

    # --- evidence --------------------------------------------------------------------

    def upsert_evidence(self, user_id: str, chunk: EvidenceChunk) -> StoredEvidence:
        with self._session_factory() as session:
            stored = self._upsert_evidence_row(session, user_id, chunk)
            session.commit()
            session.refresh(stored)
            return _to_evidence(stored)

    def get_evidence(self, evidence_id: int) -> StoredEvidence | None:
        with self._session_factory() as session:
            row = session.get(EvidenceRow, evidence_id)
            return _to_evidence(row) if row is not None else None

    def assign_evidence(self, evidence_id: int, experience_id: int | None) -> StoredEvidence:
        with self._session_factory() as session:
            row = session.get(EvidenceRow, evidence_id)
            if row is None:
                raise LookupError(f"no evidence with id {evidence_id}")
            row.experience_id = experience_id
            session.commit()
            session.refresh(row)
            return _to_evidence(row)

    def list_assigned_evidence(self, user_id: str, experience_id: int) -> list[StoredEvidence]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(EvidenceRow)
                .where(
                    EvidenceRow.user_id == user_id,
                    EvidenceRow.experience_id == experience_id,
                )
                .order_by(EvidenceRow.id)
            )
            return [_to_evidence(row) for row in rows]

    @staticmethod
    def _upsert_evidence_row(session: Session, user_id: str, chunk: EvidenceChunk) -> EvidenceRow:
        row = session.scalar(
            select(EvidenceRow).where(
                EvidenceRow.user_id == user_id,
                EvidenceRow.source_type == chunk.source_type,
                EvidenceRow.source_ref == chunk.source_ref,
            )
        )
        if row is not None:
            if row.chunk_text != chunk.chunk_text:
                row.chunk_text = chunk.chunk_text
            return row
        row = EvidenceRow(
            user_id=user_id,
            source_type=chunk.source_type,
            source_ref=chunk.source_ref,
            chunk_text=chunk.chunk_text,
        )
        session.add(row)
        session.flush()
        return row

    # --- claims ----------------------------------------------------------------------

    def replace_unreviewed_claims(
        self, user_id: str, experience_id: int, claims: Sequence[StorableClaim]
    ) -> list[Claim]:
        with self._session_factory() as session:
            existing = session.scalars(
                select(ClaimRow).where(
                    ClaimRow.user_id == user_id, ClaimRow.experience_id == experience_id
                )
            ).all()
            reviewed_fingerprints = set()
            for row in existing:
                if row.status in _UNREVIEWED:
                    for link in session.scalars(
                        select(ClaimEvidenceRow).where(ClaimEvidenceRow.claim_id == row.id)
                    ):
                        session.delete(link)
                    session.delete(row)
                else:
                    reviewed_fingerprints.add(
                        claim_content_fingerprint(
                            row.problem_text, row.action_text, row.result_text
                        )
                    )
            session.flush()

            inserted: list[Claim] = []
            for storable in claims:
                draft = storable.draft
                fingerprint = claim_content_fingerprint(
                    draft.problem_text, draft.action_text, draft.result_text
                )
                if fingerprint in reviewed_fingerprints:
                    continue  # a human already decided on this content; never re-queue
                row = ClaimRow(
                    user_id=user_id,
                    experience_id=experience_id,
                    status=storable.status.value,
                    action_text=draft.action_text,
                    action_tools=list(draft.action_tools),
                    problem_text=draft.problem_text,
                    problem_cost_dimension=(
                        draft.problem_cost_dimension.value if draft.problem_cost_dimension else None
                    ),
                    problem_inefficiency=(
                        draft.problem_inefficiency.value if draft.problem_inefficiency else None
                    ),
                    result_text=draft.result_text,
                    result_kind=draft.result_kind.value,
                    result_status=storable.result_status.value,
                    result_metric_json=draft.result_metric_json,
                    validation_flags=list(storable.validation_flags),
                )
                session.add(row)
                session.flush()
                link_rows = []
                for ref in draft.evidence:
                    evidence_row = self._upsert_evidence_row(session, user_id, ref.chunk)
                    link = ClaimEvidenceRow(
                        claim_id=row.id,
                        evidence_id=evidence_row.id,
                        field=ref.field.value,
                        outcome_quote=ref.outcome_quote,
                    )
                    session.add(link)
                    link_rows.append(link)
                session.flush()
                inserted.append(_to_claim(row, link_rows))
            session.commit()
            return inserted

    def get_claim(self, claim_id: int) -> Claim | None:
        with self._session_factory() as session:
            row = session.get(ClaimRow, claim_id)
            if row is None:
                return None
            return _to_claim(row, self._links(session, row.id))

    def list_claims(self, user_id: str, status: ClaimStatus | None = None) -> list[Claim]:
        with self._session_factory() as session:
            query = select(ClaimRow).where(ClaimRow.user_id == user_id).order_by(ClaimRow.id)
            if status is not None:
                query = query.where(ClaimRow.status == status.value)
            return [_to_claim(row, self._links(session, row.id)) for row in session.scalars(query)]

    def apply_claim_edit(self, user_id: str, plan: ClaimEditPlan) -> Claim:
        with self._session_factory() as session:
            row = session.get(ClaimRow, plan.claim_id)
            if row is None:
                raise LookupError(f"no claim with id {plan.claim_id}")
            for attribute, value in plan.updates.items():
                setattr(row, attribute, _to_column_value(value))
            for attestation in plan.attestations:
                evidence_row = self._upsert_evidence_row(session, user_id, attestation.chunk)
                existing_link = session.scalar(
                    select(ClaimEvidenceRow).where(
                        ClaimEvidenceRow.claim_id == row.id,
                        ClaimEvidenceRow.evidence_id == evidence_row.id,
                        ClaimEvidenceRow.field == attestation.field.value,
                    )
                )
                if existing_link is not None:
                    existing_link.outcome_quote = attestation.outcome_quote
                else:
                    session.add(
                        ClaimEvidenceRow(
                            claim_id=row.id,
                            evidence_id=evidence_row.id,
                            field=attestation.field.value,
                            outcome_quote=attestation.outcome_quote,
                        )
                    )
            session.commit()
            session.refresh(row)
            return _to_claim(row, self._links(session, row.id))

    def transition_claim(
        self,
        claim_id: int,
        new_status: ClaimStatus,
        *,
        review_note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> Claim:
        with self._session_factory() as session:
            row = session.get(ClaimRow, claim_id)
            if row is None:
                raise LookupError(f"no claim with id {claim_id}")
            validate_claim_transition(ClaimStatus(row.status), new_status, review_note=review_note)
            row.status = new_status.value
            if review_note is not None:
                row.review_note = review_note
            row.reviewed_at = reviewed_at or datetime.now(tz=UTC)
            session.commit()
            session.refresh(row)
            return _to_claim(row, self._links(session, row.id))

    @staticmethod
    def _links(session: Session, claim_id: int) -> list[ClaimEvidenceRow]:
        return list(
            session.scalars(
                select(ClaimEvidenceRow)
                .where(ClaimEvidenceRow.claim_id == claim_id)
                .order_by(ClaimEvidenceRow.id)
            )
        )
