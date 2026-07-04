"""Verify the Alembic migration builds the oauth_credentials schema (offline SQLite)."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.db.credentials_store import SqlOAuthCredentialStore
from app.db.session import create_session_factory
from app.domain.credentials import PROVIDER_GDRIVE, OAuthCredential
from app.security.crypto import TokenCipher

EXPECTED_COLUMNS = {
    "id",
    "user_id",
    "provider",
    "account_label",
    "encrypted_access_token",
    "encrypted_refresh_token",
    "scopes",
    "expires_at",
    "updated_at",
}


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _sqlite_url(tmp_path: Path, name: str) -> str:
    return f"sqlite+pysqlite:///{tmp_path / name}"


def test_upgrade_creates_schema_then_downgrade_drops_it(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "up.db")
    cfg = _config(url)

    command.upgrade(cfg, "head")
    inspector = sa.inspect(sa.create_engine(url))
    assert "oauth_credentials" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("oauth_credentials")}
    assert columns >= EXPECTED_COLUMNS
    uniques = {u["name"] for u in inspector.get_unique_constraints("oauth_credentials")}
    assert "uq_oauth_user_provider" in uniques
    indexes = {i["name"] for i in inspector.get_indexes("oauth_credentials")}
    assert "ix_oauth_credentials_user_id" in indexes

    command.downgrade(cfg, "base")
    assert "oauth_credentials" not in sa.inspect(sa.create_engine(url)).get_table_names()


def test_upgrade_creates_master_cv_and_cv_sources(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "cv.db")
    command.upgrade(_config(url), "head")
    tables = set(sa.inspect(sa.create_engine(url)).get_table_names())
    assert {"master_cv", "cv_sources"} <= tables


def test_upgrade_creates_jobs_and_matches(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "jobs.db")
    command.upgrade(_config(url), "head")
    tables = set(sa.inspect(sa.create_engine(url)).get_table_names())
    assert {"jobs", "job_matches"} <= tables


def test_upgrade_creates_interviews_and_prep_packets(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "interviews.db")
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))
    assert {"interviews", "prep_packets"} <= set(inspector.get_table_names())
    uniques = {u["name"] for u in inspector.get_unique_constraints("interviews")}
    assert "uq_interviews_user_message" in uniques


def test_upgrade_creates_applications_and_outreach(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "apps.db")
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))
    assert {"applications", "outreach"} <= set(inspector.get_table_names())
    app_uniques = {u["name"] for u in inspector.get_unique_constraints("applications")}
    assert "uq_applications_user_job" in app_uniques
    outreach_uniques = {u["name"] for u in inspector.get_unique_constraints("outreach")}
    assert "uq_outreach_application" in outreach_uniques


def test_upgrade_creates_claims_schema_and_downgrade_drops_it(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "claims.db")
    cfg = _config(url)
    command.upgrade(cfg, "head")

    inspector = sa.inspect(sa.create_engine(url))
    v2_tables = {"evidence", "claims", "claim_evidence", "experiences"}
    assert v2_tables <= set(inspector.get_table_names())

    claims_columns = {c["name"] for c in inspector.get_columns("claims")}
    assert {
        "status",
        "problem_cost_dimension",
        "problem_inefficiency",  # v2.2 delta
        "action_tools",
        "result_kind",
        "result_status",
        "result_metric_json",
        "validation_flags",
        "review_note",
    } <= claims_columns
    experience_columns = {c["name"] for c in inspector.get_columns("experiences")}
    assert {"section", "sort_order"} <= experience_columns  # v2.2 delta
    link_columns = {c["name"] for c in inspector.get_columns("claim_evidence")}
    assert "outcome_quote" in link_columns  # v2.2 delta

    evidence_uniques = {u["name"] for u in inspector.get_unique_constraints("evidence")}
    assert "uq_evidence_user_source" in evidence_uniques
    experience_uniques = {u["name"] for u in inspector.get_unique_constraints("experiences")}
    assert "uq_experiences_user_name" in experience_uniques

    command.downgrade(cfg, "0005")
    remaining = set(sa.inspect(sa.create_engine(url)).get_table_names())
    assert not (v2_tables & remaining)


def test_upgrade_renames_interview_provenance_and_creates_validation_runs(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path, "provenance.db")
    cfg = _config(url)
    command.upgrade(cfg, "head")

    inspector = sa.inspect(sa.create_engine(url))
    interview_columns = {c["name"] for c in inspector.get_columns("interviews")}
    assert "gmail_message_id" in interview_columns
    assert "evidence_quote" in interview_columns
    assert "source_message_id" not in interview_columns
    uniques = {u["name"] for u in inspector.get_unique_constraints("interviews")}
    assert "uq_interviews_user_message" in uniques
    assert "validation_runs" in inspector.get_table_names()
    run_columns = {c["name"] for c in inspector.get_columns("validation_runs")}
    assert {"kind", "subject_ref", "passed", "detail"} <= run_columns

    command.downgrade(cfg, "0006")
    inspector = sa.inspect(sa.create_engine(url))
    downgraded = {c["name"] for c in inspector.get_columns("interviews")}
    assert "source_message_id" in downgraded
    assert "gmail_message_id" not in downgraded
    assert "validation_runs" not in inspector.get_table_names()


def test_upgrade_creates_artifacts(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "artifacts.db")
    cfg = _config(url)
    command.upgrade(cfg, "head")
    inspector = sa.inspect(sa.create_engine(url))
    assert "artifacts" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("artifacts")}
    assert {"kind", "file_path", "master_cv_version"} <= columns
    uniques = {u["name"] for u in inspector.get_unique_constraints("artifacts")}
    assert "uq_artifacts_user_kind_version" in uniques
    command.downgrade(cfg, "0007")
    assert "artifacts" not in sa.inspect(sa.create_engine(url)).get_table_names()


def test_sql_store_round_trips_on_migrated_schema(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "store.db")
    command.upgrade(_config(url), "head")

    store = SqlOAuthCredentialStore(
        create_session_factory(sa.create_engine(url)),
        TokenCipher(TokenCipher.generate_key()),
    )
    store.upsert(OAuthCredential("u1", PROVIDER_GDRIVE, "jordan@example.com", "tok"))
    got = store.get("u1", PROVIDER_GDRIVE)
    assert got is not None and got.access_token == "tok"
