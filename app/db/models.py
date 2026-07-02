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
