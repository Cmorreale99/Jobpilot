"""Run roster detection against the CONFIGURED sources — real or mock.

``python -m app.tools.run_roster_detection``

Gathers every policy-approved source (normalized), proposes the real-world entity
roster (LLM-backed when ``ROSTER_LLM_DETECTION=true``), and lands NEW entities as
``proposed`` — nothing becomes a boundary until you confirm it on the dashboard's
roster section. Idempotent: existing entities (any status) are never changed, and a
discarded entity is never re-proposed.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.db.claim_repository import SqlClaimRepository
from app.db.session import create_all, create_db_engine, create_session_factory
from app.integrations.drive_factory import create_drive_client
from app.integrations.github_factory import create_github_client
from app.services.roster import run_roster_detection


async def _run() -> None:
    settings = get_settings()
    engine = create_db_engine(settings)
    create_all(engine)

    report = await run_roster_detection(
        create_drive_client(settings),
        create_github_client(settings),
        settings.pipeline_user_id,
        SqlClaimRepository(create_session_factory(engine)),
        settings,
    )
    print(f"documents read: {report.documents}")
    print(f"new proposals awaiting review: {len(report.proposed)}")
    for experience in report.proposed:
        dates = f" ({experience.dates})" if experience.dates else ""
        print(f"  - [{experience.kind.value}] {experience.name}{dates}")
    print("confirm/merge/rename/discard on the dashboard, then run the extraction tool.")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
