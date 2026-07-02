"""End-to-end drafting service on mocks: matches -> tailored drafts in the approval queue."""

from __future__ import annotations

from app.config import Settings
from app.domain.applications import ApplicationStatus, OutreachStatus
from app.domain.cv import MasterCv, ParClaim
from app.domain.jobs import Job, JobMatch
from app.integrations.mock.research import MockResearchClient
from app.services.application_repository import InMemoryApplicationRepository
from app.services.outreach import run_drafting


def _cv() -> MasterCv:
    return MasterCv(
        claims=[
            ParClaim(
                action="Re-architected the settlement pipeline into idempotent sharded "
                "workers over a Kafka event log in Go and Python",
                source_ref="resume",
                result="Cut settlement runtime by 70%",
            ),
            ParClaim(
                action="Built realtime fraud scoring features with Flink over Kafka",
                source_ref="github",
            ),
        ],
        version=2,
    )


def _match(job: Job, rank: int) -> JobMatch:
    return JobMatch(job=job, score=0.9, rank=rank, rationale="fits", matched_terms=("kafka",))


_LEDGERLINE = Job(
    source="mock",
    external_id="job-1001",
    title="Staff Backend Engineer, Payments",
    company="Ledgerline",
    description="Idempotent settlement workers over a Kafka event log in Go and Python.",
)
_NORTHWIND = Job(
    source="mock",
    external_id="job-1003",
    title="Platform Engineer, Payments Ledger",
    company="Northwind",  # deliberately absent from the contacts fixture
    description="Payments ledger platform work with Kafka.",
)


async def test_run_drafting_queues_one_draft_per_match(
    mock_research_client: MockResearchClient, settings: Settings
) -> None:
    repo = InMemoryApplicationRepository()
    matches = [_match(_LEDGERLINE, 1), _match(_NORTHWIND, 2)]

    records = await run_drafting("u1", _cv(), matches, mock_research_client, repo, settings)

    assert len(records) == 2
    assert all(r.status is OutreachStatus.DRAFTED for r in records)
    assert repo.list_pending_outreach("u1") == records
    applications = repo.list_applications("u1")
    assert {a.job_external_id for a in applications} == {"job-1001", "job-1003"}
    assert all(a.status is ApplicationStatus.DRAFTED for a in applications)
    assert all(a.materials.highlights for a in applications)


async def test_researched_contact_used_and_absent_contact_stays_generic(
    mock_research_client: MockResearchClient, settings: Settings
) -> None:
    repo = InMemoryApplicationRepository()
    records = await run_drafting(
        "u1",
        _cv(),
        [_match(_LEDGERLINE, 1), _match(_NORTHWIND, 2)],
        mock_research_client,
        repo,
        settings,
    )
    by_company = {repo.get_application(r.application_id).job_company: r for r in records}  # type: ignore[union-attr]
    assert by_company["Ledgerline"].contact is not None
    assert by_company["Ledgerline"].contact.name == "Priya Raman"
    assert by_company["Northwind"].contact is None
    assert "hiring team" in by_company["Northwind"].body


async def test_rerun_is_idempotent(
    mock_research_client: MockResearchClient, settings: Settings
) -> None:
    repo = InMemoryApplicationRepository()
    matches = [_match(_LEDGERLINE, 1)]
    first = await run_drafting("u1", _cv(), matches, mock_research_client, repo, settings)
    second = await run_drafting("u1", _cv(), matches, mock_research_client, repo, settings)
    assert [r.id for r in first] == [r.id for r in second]
    assert len(repo.list_applications("u1")) == 1
    assert len(repo.list_pending_outreach("u1")) == 1


async def test_rerun_skips_applications_the_user_acted_on(
    mock_research_client: MockResearchClient, settings: Settings
) -> None:
    repo = InMemoryApplicationRepository()
    matches = [_match(_LEDGERLINE, 1)]
    await run_drafting("u1", _cv(), matches, mock_research_client, repo, settings)
    application = repo.list_applications("u1")[0]
    repo.transition_application(application.id, ApplicationStatus.APPLIED)

    records = await run_drafting("u1", _cv(), matches, mock_research_client, repo, settings)

    assert records == []  # acted-on application skipped entirely
    assert repo.get_application(application.id).status is ApplicationStatus.APPLIED  # type: ignore[union-attr]


async def test_rerun_never_resurrects_a_discarded_draft(
    mock_research_client: MockResearchClient, settings: Settings
) -> None:
    repo = InMemoryApplicationRepository()
    matches = [_match(_LEDGERLINE, 1)]
    first = await run_drafting("u1", _cv(), matches, mock_research_client, repo, settings)
    repo.transition_outreach(first[0].id, OutreachStatus.DISCARDED)

    await run_drafting("u1", _cv(), matches, mock_research_client, repo, settings)

    assert repo.list_pending_outreach("u1") == []
    record = repo.get_outreach(first[0].id)
    assert record is not None and record.status is OutreachStatus.DISCARDED
