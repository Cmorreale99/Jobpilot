"""SQLAlchemy-backed :class:`JobRepository`.

Jobs dedupe on ``(source, external_id)``; matches are stored per ``(user_id, Master CV
version)`` with replace semantics so re-running the matcher is idempotent.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import JobMatchRow, JobRow
from app.domain.jobs import Job, JobMatch


def _to_job(row: JobRow) -> Job:
    return Job(
        source=row.source,
        external_id=row.external_id,
        title=row.title,
        company=row.company,
        description=row.description,
        location=row.location,
        url=row.url,
        posted_at=row.posted_at,
        remote=row.remote,
    )


class SqlJobRepository:
    """Job + match persistence over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_jobs(self, jobs: list[Job]) -> int:
        with self._session_factory() as session:
            for job in jobs:
                existing = session.scalar(
                    select(JobRow).where(
                        JobRow.source == job.source, JobRow.external_id == job.external_id
                    )
                )
                if existing is None:
                    session.add(
                        JobRow(
                            source=job.source,
                            external_id=job.external_id,
                            title=job.title,
                            company=job.company,
                            description=job.description,
                            location=job.location,
                            url=job.url,
                            posted_at=job.posted_at,
                            remote=job.remote,
                        )
                    )
                else:
                    existing.title = job.title
                    existing.company = job.company
                    existing.description = job.description
                    existing.location = job.location
                    existing.url = job.url
                    existing.posted_at = job.posted_at
                    existing.remote = job.remote
            session.commit()
        return len(jobs)

    def save_matches(self, user_id: str, master_cv_version: int, matches: list[JobMatch]) -> None:
        with self._session_factory() as session:
            session.execute(
                delete(JobMatchRow).where(
                    JobMatchRow.user_id == user_id,
                    JobMatchRow.master_cv_version == master_cv_version,
                )
            )
            for match in matches:
                session.add(
                    JobMatchRow(
                        user_id=user_id,
                        master_cv_version=master_cv_version,
                        job_source=match.job.source,
                        job_external_id=match.job.external_id,
                        score=match.score,
                        rank=match.rank,
                        stage=match.stage,
                        rationale=match.rationale,
                        matched_terms=list(match.matched_terms),
                    )
                )
            session.commit()

    def get_matches(self, user_id: str, master_cv_version: int) -> list[JobMatch]:
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(JobMatchRow)
                    .where(
                        JobMatchRow.user_id == user_id,
                        JobMatchRow.master_cv_version == master_cv_version,
                    )
                    .order_by(JobMatchRow.rank)
                )
            )
            matches: list[JobMatch] = []
            for row in rows:
                job_row = session.scalar(
                    select(JobRow).where(
                        JobRow.source == row.job_source,
                        JobRow.external_id == row.job_external_id,
                    )
                )
                if job_row is None:
                    continue
                matches.append(
                    JobMatch(
                        job=_to_job(job_row),
                        score=row.score,
                        rank=row.rank,
                        rationale=row.rationale,
                        stage=row.stage,
                        matched_terms=tuple(row.matched_terms),
                    )
                )
            return matches
