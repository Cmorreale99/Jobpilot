"""Tailoring + outreach drafting orchestration: matches → materials → approval queue.

For each deep-ranked match: tailor materials from the Master CV, create (or refresh) the
application, research a contact, draft outreach, and upsert the draft into the approval
queue. Depends only on the :class:`ResearchClient` and :class:`ApplicationRepository`
interfaces plus the drafter protocols, so it runs identically on mocks and real clients.

**Idempotent by construction:** re-running over the same matches refreshes *drafted*
rows and skips everything a human has acted on — it never duplicates applications or
drafts, and never re-queues an approved, sent, or discarded message. Nothing here sends
anything; sending arrives with the real mail client (M6) and stays behind approval.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.domain.applications import ApplicationRepository, ApplicationStatus, OutreachRecord
from app.domain.cv import MasterCv
from app.domain.jobs import JobMatch
from app.domain.outreach import OutreachDrafter
from app.domain.tailoring import MaterialsTailorer
from app.integrations.base import ResearchClient
from app.services.drafting_factory import create_drafters

logger = logging.getLogger(__name__)


async def run_drafting(
    user_id: str,
    master_cv: MasterCv,
    matches: list[JobMatch],
    research_client: ResearchClient,
    repository: ApplicationRepository,
    settings: Settings | None = None,
    *,
    tailorer: MaterialsTailorer | None = None,
    drafter: OutreachDrafter | None = None,
) -> list[OutreachRecord]:
    """Draft tailored outreach for every match; return the resulting queue records.

    Matches whose application is no longer ``drafted`` are skipped entirely (the user
    has already applied to / ignored that job).
    """
    settings = settings or get_settings()
    if settings.outreach_auto_send:
        logger.warning(
            "OUTREACH_AUTO_SEND is enabled but sending is not implemented until M6; "
            "drafts remain in the approval queue."
        )
    if tailorer is None or drafter is None:
        default_tailorer, default_drafter = create_drafters(settings)
        tailorer = tailorer or default_tailorer
        drafter = drafter or default_drafter

    records: list[OutreachRecord] = []
    for match in matches:
        materials = tailorer.tailor(master_cv, match)
        application = repository.get_or_create_application(
            user_id, match.job, master_cv.version, materials
        )
        if application.status is not ApplicationStatus.DRAFTED:
            logger.info(
                "skipping %s @ %s: application already '%s'",
                match.job.title,
                match.job.company,
                application.status,
            )
            continue
        contact = await research_client.find_contact(match.job)
        message = drafter.draft(materials, match.job, contact)
        records.append(repository.upsert_draft(application.id, message, contact))
    return records
