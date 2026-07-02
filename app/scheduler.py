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
from app.services.pipeline import (
    PipelineDependencies,
    build_default_dependencies,
    run_application_pipeline,
)

logger = logging.getLogger(__name__)

APPLICATION_PIPELINE_JOB_ID = "application-pipeline"


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
) -> AsyncIOScheduler:
    """Build the scheduler with the nightly application-pipeline trigger registered.

    Dependencies are built lazily at first fire (not at scheduler construction), so the
    process starts with zero credentials and no database connection.
    """
    settings = settings or get_settings()
    scheduler = AsyncIOScheduler()

    async def application_pipeline_job() -> None:
        deps = dependencies or build_default_dependencies(settings)
        await run_job_safely(
            APPLICATION_PIPELINE_JOB_ID,
            lambda: run_application_pipeline(deps, settings),
        )

    scheduler.add_job(
        application_pipeline_job,
        CronTrigger(hour=settings.pipeline_hour, minute=settings.pipeline_minute),
        id=APPLICATION_PIPELINE_JOB_ID,
        name="Nightly application pipeline (CV refresh → matching → outreach drafts)",
    )
    return scheduler


async def _serve(settings: Settings) -> None:
    scheduler = create_scheduler(settings)
    scheduler.start()
    logger.info(
        "scheduler running; application pipeline fires daily at %02d:%02d",
        settings.pipeline_hour,
        settings.pipeline_minute,
    )
    await asyncio.Event().wait()  # run until interrupted


async def _run_once(settings: Settings, dependencies: PipelineDependencies | None) -> int:
    deps = dependencies or build_default_dependencies(settings)
    result = await run_job_safely(
        APPLICATION_PIPELINE_JOB_ID, lambda: run_application_pipeline(deps, settings)
    )
    return 0 if result is not None else 1


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    dependencies: PipelineDependencies | None = None,
) -> int:
    """CLI entrypoint. ``--once`` runs the pipeline now; default schedules it nightly."""
    parser = argparse.ArgumentParser(description="JobPilot nightly scheduler")
    parser.add_argument(
        "--once", action="store_true", help="run the application pipeline once and exit"
    )
    args = parser.parse_args(argv)
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.once:
        return asyncio.run(_run_once(settings, dependencies))

    try:
        asyncio.run(_serve(settings))
    except KeyboardInterrupt:
        logger.info("scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
