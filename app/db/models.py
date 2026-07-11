"""ORM models: ``oauth_credentials``, ``master_cv``, ``source_documents``, ...

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


class SourceDocumentRow(Base):
    """One captured source's identity + latest metadata (canonical capture, H2).

    Replaces the dead V1 ``cv_sources`` table (dropped in migration ``0017`` — it never
    had a writer). One row per ``(user_id, source_type, source_ref)``; the raw payloads
    live in ``source_document_versions``.
    """

    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "source_ref", name="uq_source_doc_user_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    modified_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceDocumentVersionRow(Base):
    """One immutable raw payload of a captured source (canonical capture, H2).

    ``raw_text`` is the text exactly as the client returned it — **pre-normalization**.
    Never updated or deleted after insert; only ``is_active`` moves (exactly one active
    version per document; content returning to an earlier hash re-activates that row).
    ``normalization_version`` records which normalizer generation downstream spans of
    this payload were taken under.
    """

    __tablename__ = "source_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "content_hash", name="uq_source_version_doc_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str] = mapped_column(Text)
    extractor: Mapped[str] = mapped_column(String(128))
    normalization_version: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # H4 element derivation: which structurer generation produced this version's
    # element tree, and whether it reconciled (ok) or left characters unaccounted
    # (failed). NULL = no structuring pass yet.
    structurer_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingestion_status: Mapped[str | None] = mapped_column(String(16), nullable=True)


class SourceElementRow(Base):
    """One structural element of a captured source version (canonical structure, H4).

    A pure, replaceable derivation of the version's immutable ``raw_text``:
    ``raw_start``/``raw_end`` slice it verbatim, ``parent_element_id`` carries the
    heading hierarchy, ``sequence_index`` the document order — the structure that
    chunking and assignment consume (H5) instead of re-inferring.
    """

    __tablename__ = "source_elements"
    __table_args__ = (
        UniqueConstraint("document_version_id", "sequence_index", name="uq_element_version_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(Integer, index=True)
    sequence_index: Mapped[int] = mapped_column(Integer)
    parent_element_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    element_type: Mapped[str] = mapped_column(String(32))
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_start: Mapped[int] = mapped_column(Integer)
    raw_end: Mapped[int] = mapped_column(Integer)
    normalized_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    # Explicit per-element disposition (ok|unsupported|parser_error) — the text
    # scanner only emits ok; the V4 binary parsers share this schema and the rule
    # that nothing is silently dropped.
    extraction_status: Mapped[str] = mapped_column(String(16), default="ok")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    canonical_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
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
    """A roster entity (employer role or project) grouping claims.

    ``section``/``sort_order`` are user-assigned render placement; ``kind``/``status``/
    ``aliases`` are the roster layer — detection proposes, a human confirms, and only
    confirmed entities scope extraction.
    """

    __tablename__ = "experiences"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_experiences_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(512))
    subtitle: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dates: Mapped[str | None] = mapped_column(String(128), nullable=True)
    section: Mapped[str] = mapped_column(String(32), default="professional_experience")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(32), default="project", server_default="project")
    status: Mapped[str] = mapped_column(String(16), default="confirmed", server_default="confirmed")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Fingerprint of the evidence last successfully extracted; matching re-runs skip
    # the group (NULL = never extracted or last attempt failed — always run).
    extraction_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # Roster assignment: the confirmed experience this chunk belongs to (nullable —
    # unassigned chunks never feed extraction).
    experience_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Which normalizer generation produced this chunk's text: `#chars=` span refs are
    # offsets into normalizer OUTPUT, so a rule change must be detectable instead of
    # silently dangling every span (V3 §2.2). NULL = written before versioning.
    normalization_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # HOW the current assignment was made (heuristic|llm|readme_ref|repo_ref|human|
    # section — documented, not a CHECK constraint). `human` rows are pinned: machine
    # re-runs never overwrite them (hardening H1). NULL = legacy/unlabeled (machine).
    assignment_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class ProjectStoryRow(Base):
    """One project story per (confirmed roster entity, problem space) — V3 story layer
    (§2.2), generalized by v3.1 Increment 3.

    ``review_status`` is the only lifecycle state (draft|pending_review|approved|
    excluded); readiness is derived at read time and deliberately has no column.
    Components cite claim ids only — ``problem_refs``/``actions_json``/``results_json``
    hold presentation plus those ids, never bare evidence refs. ``problem_space_id``
    is the detected space's stable content-hash id (or the entity's leftover space);
    ``selected_action_id``/``selected_result_id``/``bundle_status`` are the v3.1
    selection state (Increment 4's flow stamps the selection; synthesis only ever
    derives ``requires_user_selection``/``missing_result``).
    """

    __tablename__ = "project_stories"
    __table_args__ = (
        UniqueConstraint(
            "experience_id", "problem_space_id", name="uq_project_stories_experience_space"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    experience_id: Mapped[int] = mapped_column(Integer)
    problem_space_id: Mapped[str] = mapped_column(String(64))
    problem_space_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem_space_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_status: Mapped[str] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(16))
    problem_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem_refs: Mapped[list[int]] = mapped_column(JSON, default=list)
    actions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    results_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Fingerprint of the synthesis inputs (claims + assigned evidence); matching
    # re-runs skip the entity, mirroring experiences.extraction_hash.
    synthesis_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProblemSpaceGroupingRow(Base):
    """One recorded problem-space partition (v3.1 Increment 8).

    Keyed by the problem-set fingerprint (``grouping_fingerprint`` — includes the
    detector's version namespace), storing the sanitized groups as normalized problem
    keys. First write wins: synthesis, eval, and re-runs over the same problems
    replay this row instead of re-asking a non-deterministic grouper.
    """

    __tablename__ = "problem_space_groupings"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_groupings_user_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    groups_json: Mapped[list[list[str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtifactRow(Base):
    """One generated file (V2: only ``master_cv_docx``), deduped per rendered version."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "kind", "master_cv_version", name="uq_artifacts_user_kind_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    file_path: Mapped[str] = mapped_column(String(1024))
    master_cv_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
