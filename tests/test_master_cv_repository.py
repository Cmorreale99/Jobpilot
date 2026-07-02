"""Master CV repository: versioning, idempotency, dedupe (in-memory + SQL)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from app.db.master_cv_repository import SqlMasterCvRepository
from app.db.session import create_all, create_session_factory
from app.domain.cv import CvSource, MasterCv, MasterCvBuilder, MasterCvRepository

INGEST_A = datetime(2026, 7, 1, tzinfo=UTC)
INGEST_B = datetime(2026, 7, 2, tzinfo=UTC)  # a later re-run


def _cv(specs: list[tuple[str, str, str]], *, ingested: datetime = INGEST_A) -> MasterCv:
    """Build a Master CV from (external_ref, title, raw_text) specs."""
    sources = [
        CvSource(
            source_type="gdrive",
            external_ref=ref,
            title=title,
            mime_type="text/plain",
            raw_text=raw,
            ingested_at=ingested,
        )
        for ref, title, raw in specs
    ]
    return MasterCvBuilder().build(sources)


_RESUME = [("drv_1", "Resume", "Action: Shipped the thing.\nResult: It worked.")]
_RESUME_EDITED = [("drv_1", "Resume", "Action: Shipped a better thing.\nResult: It worked.")]


@pytest.fixture(params=["memory", "sql"])
def repo(request: pytest.FixtureRequest) -> Iterator[MasterCvRepository]:
    if request.param == "memory":
        from app.services.master_cv_repository import InMemoryMasterCvRepository

        yield InMemoryMasterCvRepository()
    else:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
        create_all(engine)
        yield SqlMasterCvRepository(create_session_factory(engine))


def test_save_creates_version_one(repo: MasterCvRepository) -> None:
    stored = repo.save("u1", _cv(_RESUME))
    assert stored.version == 1
    assert repo.get_latest("u1") is not None
    assert repo.list_versions("u1") == [1]


def test_resave_same_content_is_idempotent(repo: MasterCvRepository) -> None:
    repo.save("u1", _cv(_RESUME, ingested=INGEST_A))
    # Re-run with a later ingested_at but identical evidence -> no new version.
    again = repo.save("u1", _cv(_RESUME, ingested=INGEST_B))
    assert again.version == 1
    assert repo.list_versions("u1") == [1]


def test_changed_content_creates_new_version(repo: MasterCvRepository) -> None:
    repo.save("u1", _cv(_RESUME))
    stored = repo.save("u1", _cv(_RESUME_EDITED))
    assert stored.version == 2
    assert repo.list_versions("u1") == [1, 2]
    assert repo.get_version("u1", 1) is not None
    assert repo.get_latest("u1").version == 2  # type: ignore[union-attr]


def test_reconstructed_cv_preserves_claims_and_provenance(repo: MasterCvRepository) -> None:
    repo.save("u1", _cv(_RESUME))
    latest = repo.get_latest("u1")
    assert latest is not None
    assert latest.master_cv.claims
    assert {s.external_ref for s in latest.master_cv.sources} == {"drv_1"}
    assert all(c.source_ref == "drv_1" for c in latest.master_cv.claims)


def test_versions_are_isolated_per_user(repo: MasterCvRepository) -> None:
    repo.save("u1", _cv(_RESUME))
    repo.save("u2", _cv(_RESUME_EDITED))
    assert repo.get_latest("u1").master_cv.version == 1  # type: ignore[union-attr]
    assert repo.list_versions("u2") == [1]


def test_sql_repo_dedupes_cv_sources() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    repo = SqlMasterCvRepository(create_session_factory(engine))

    repo.save("u1", _cv(_RESUME))
    repo.save("u1", _cv(_RESUME_EDITED))  # same source ref, new content -> version 2

    with engine.connect() as conn:
        source_rows = conn.execute(sa.text("SELECT COUNT(*) FROM cv_sources")).scalar_one()
        cv_rows = conn.execute(sa.text("SELECT COUNT(*) FROM master_cv")).scalar_one()
    assert source_rows == 1  # one source, updated in place — not duplicated
    assert cv_rows == 2  # two versions retained
