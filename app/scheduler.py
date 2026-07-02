"""Nightly trigger wiring — ``uv run python -m app.scheduler``.

Deliberately thin: APScheduler appears here and nowhere else, and each trigger just
awaits a service function, so the business logic ports to EventBridge→Lambda untouched.

Jobs are isolated: :func:`run_job_safely` logs a failure and swallows it, so one broken
nightly job can never take down the scheduler or block another job (the M7 interview
scan will register alongside the application pipeline with the same wrapper).

``--once`` runs the application pipeline immediately and exits — the dev/debug path.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings, get_settings
from app.services.interview_scan import (
    InterviewScanDependencies,
    build_default_interview_dependencies,
    run_interview_scan,
)
from app.services.pipeline import (
    PipelineDependencies,
    build_default_dependencies,
    run_application_pipeline,
)

logger = logging.getLogger(__name__)

APPLICATION_PIPELINE_JOB_ID = "application-pipeline"
INTERVIEW_SCAN_JOB_ID = "interview-scan"


async def run_job_safely[T](name: str, coro_factory: Callable[[], Awaitable[T]], /) -> T | None:
    """Await one job, logging (never propagating) a failure — job isolation."""
    try:
        result = await coro_factory()
    except Exception:
        logger.exception("nightly job %r failed; other jobs are unaffected", name)
        return None
    logger.info("nightly job %r finished: %s", name, result)
    return result


def create_scheduler(
    settings: Settings | None = None,
    dependencies: PipelineDependencies | None = None,
    interview_dependencies: InterviewScanDependencies | None = None,
) -> AsyncIOScheduler:
    """Build the scheduler with both independent nightly triggers registered.

    Dependencies are built lazily at first fire (not at scheduler construction), so the
    process starts with zero credentials and no database connection. Each job runs
    through :func:`run_job_safely`, so a failure in one never blocks the other.
    """
    settings = settings or get_settings()
    scheduler = AsyncIOScheduler()

    async def _run_pipeline() -> object:
        # Dependency building happens INSIDE the safe wrapper: a configuration error
        # (bad credential, unreachable server) is a failed job, not a dead scheduler.
        deps = dependencies or build_default_dependencies(settings)
        return await run_application_pipeline(deps, settings)

    async def _run_scan() -> object:
        deps = interview_dependencies or build_default_interview_dependencies(settings)
        return await run_interview_scan(deps, settings)

    async def application_pipeline_job() -> None:
        await run_job_safely(APPLICATION_PIPELINE_JOB_ID, _run_pipeline)

    async def interview_scan_job() -> None:
        await run_job_safely(INTERVIEW_SCAN_JOB_ID, _run_scan)

    scheduler.add_job(
        application_pipeline_job,
        CronTrigger(hour=settings.pipeline_hour, minute=settings.pipeline_minute),
        id=APPLICATION_PIPELINE_JOB_ID,
        name="Nightly application pipeline (CV refresh → matching → outreach drafts)",
    )
    scheduler.add_job(
        interview_scan_job,
        CronTrigger(hour=settings.interview_scan_hour, minute=settings.interview_scan_minute),
        id=INTERVIEW_SCAN_JOB_ID,
        name="Nightly interview scan (scoped inbox → interviews → prep packets)",
    )
    return scheduler


async def _serve(settings: Settings) -> None:
    scheduler = create_scheduler(settings)
    scheduler.start()
    logger.info(
        "scheduler running; application pipeline fires daily at %02d:%02d, "
        "interview scan at %02d:%02d",
        settings.pipeline_hour,
        settings.pipeline_minute,
        settings.interview_scan_hour,
        settings.interview_scan_minute,
    )
    await asyncio.Event().wait()  # run until interrupted


async def _run_once(
    settings: Settings,
    dependencies: PipelineDependencies | None,
    interview_dependencies: InterviewScanDependencies | None,
    job: str,
) -> int:
    """Run the selected job(s) immediately; exit 0 only if everything succeeded."""

    async def _run_pipeline() -> object:
        deps = dependencies or build_default_dependencies(settings)
        return await run_application_pipeline(deps, settings)

    async def _run_scan() -> object:
        ideps = interview_dependencies or build_default_interview_dependencies(settings)
        return await run_interview_scan(ideps, settings)

    ok = True
    if job in ("pipeline", "all"):
        result = await run_job_safely(APPLICATION_PIPELINE_JOB_ID, _run_pipeline)
        ok = ok and result is not None
    if job in ("interviews", "all"):
        scan_result = await run_job_safely(INTERVIEW_SCAN_JOB_ID, _run_scan)
        ok = ok and scan_result is not None
    return 0 if ok else 1


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    dependencies: PipelineDependencies | None = None,
    interview_dependencies: InterviewScanDependencies | None = None,
) -> int:
    """CLI entrypoint. ``--once`` runs the job(s) now; default schedules them nightly."""
    parser = argparse.ArgumentParser(description="JobPilot nightly scheduler")
    parser.add_argument("--once", action="store_true", help="run the job(s) once and exit")
    parser.add_argument(
        "--job",
        choices=["pipeline", "interviews", "all"],
        default="all",
        help="which job --once runs (default: all)",
    )
    args = parser.parse_args(argv)
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.once:
        return asyncio.run(_run_once(settings, dependencies, interview_dependencies, args.job))

    try:
        asyncio.run(_serve(settings))
    except KeyboardInterrupt:
        logger.info("scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
