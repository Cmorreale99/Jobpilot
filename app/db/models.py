"""ORM models: ``oauth_credentials``, ``master_cv``, ``cv_sources``.

OAuth tokens are stored **encrypted** (the ``encrypted_*`` columns hold Fernet
ciphertext); the store layer is the only place that encrypts/decrypts. Plaintext tokens
never touch the database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OAuthCredentialRow(Base):
    """A user's encrypted OAuth credential for one provider."""

    __tablename__ = "oauth_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    account_label: Mapped[str] = mapped_column(String(320))
    encrypted_access_token: Mapped[str] = mapped_column(String)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    scopes: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MasterCvRow(Base):
    """A versioned Master CV snapshot (``content_json`` is the PAR-framed document)."""

    __tablename__ = "master_cv"
    __table_args__ = (UniqueConstraint("user_id", "version", name="uq_master_cv_user_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CvSourceRow(Base):
    """Deduped provenance for one ingested artifact, per user."""

    __tablename__ = "cv_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "external_ref", name="uq_cv_source_user_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    external_ref: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[str] = mapped_column(String(255))
    raw_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    modified_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobRow(Base):
    """A job posting, deduped on ``(source, external_id)``."""

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_jobs_source_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(1024))
    company: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationRow(Base):
    """One application per ``(user_id, job)``; status follows the domain state machine."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "job_source", "job_external_id", name="uq_applications_user_job"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    job_source: Mapped[str] = mapped_column(String(64))
    job_external_id: Mapped[str] = mapped_column(String(255))
    job_title: Mapped[str] = mapped_column(String(1024))
    job_company: Mapped[str] = mapped_column(String(512))
    master_cv_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    materials_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutreachRow(Base):
    """At most one outreach draft per application; status follows the approval queue."""

    __tablename__ = "outreach"
    __table_args__ = (UniqueConstraint("application_id", name="uq_outreach_application"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    contact_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contact_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_source: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subject: Mapped[str] = mapped_column(String(1024))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InterviewRow(Base):
    """A detected interview, deduped on ``(user_id, gmail_message_id)``.

    Hard provenance (V2): ``gmail_message_id`` NOT NULL and ``evidence_quote`` (the
    verbatim trigger text, re-verified against the provider before insert) required.
    """

    __tablename__ = "interviews"
    __table_args__ = (
        UniqueConstraint("user_id", "gmail_message_id", name="uq_interviews_user_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(255))
    evidence_quote: Mapped[str] = mapped_column(Text, default="")
    company: Mapped[str] = mapped_column(String(512))
    job_title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stage: Mapped[str] = mapped_column(String(16))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PrepPacketRow(Base):
    """The prep packet generated for one interview (one row per interview)."""

    __tablename__ = "prep_packets"
    __table_args__ = (UniqueConstraint("interview_id", name="uq_prep_packets_interview"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExperienceRow(Base):
    """An experience grouping claims; ``section``/``sort_order`` are user-assigned."""

    __tablename__ = "experiences"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_experiences_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(512))
    subtitle: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dates: Mapped[str | None] = mapped_column(String(128), nullable=True)
    section: Mapped[str] = mapped_column(String(32), default="professional_experience")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvidenceRow(Base):
    """One citable evidence chunk, deduped per user on ``(source_type, source_ref)``.

    ``source_type`` values are documented (drive|github_commit|github_pr|github_readme|
    upload|user_attestation) but deliberately NOT a CHECK constraint — the content gate
    lives in the PAR validator, where it belongs.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "source_ref", name="uq_evidence_user_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(512))
    chunk_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClaimRow(Base):
    """One PAR claim; status follows the claim state machine in ``domain/claims.py``."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    experience_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(16))
    problem_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem_cost_dimension: Mapped[str | None] = mapped_column(String(16), nullable=True)
    problem_inefficiency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action_text: Mapped[str] = mapped_column(Text)
    action_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_kind: Mapped[str] = mapped_column(String(32))
    result_status: Mapped[str] = mapped_column(String(16))
    result_metric_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClaimEvidenceRow(Base):
    """Which evidence chunk supports which PAR field of a claim.

    ``outcome_quote`` is required when ``field='result'`` (enforced by the PAR
    validator, matching the no-CHECK-constraint convention above).
    """

    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint(
            "claim_id", "evidence_id", "field", name="uq_claim_evidence_claim_evidence_field"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(Integer, index=True)
    evidence_id: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(16))
    outcome_quote: Mapped[str | None] = mapped_column(Text, nullable=True)


class ValidationRunRow(Base):
    """One recorded verification run (PAR validation or interview provenance check)."""

    __tablename__ = "validation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    subject_ref: Mapped[str] = mapped_column(String(512))
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobMatchRow(Base):
    """A deep-ranked match of a job against a user's Master CV version."""

    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "master_cv_version",
            "job_source",
            "job_external_id",
            name="uq_job_match_user_version_job",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    master_cv_version: Mapped[int] = mapped_column(Integer)
    job_source: Mapped[str] = mapped_column(String(64))
    job_external_id: Mapped[str] = mapped_column(String(255))
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    matched_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
