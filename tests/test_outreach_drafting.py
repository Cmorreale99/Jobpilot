"""Heuristic outreach drafting + the fixture-backed research client."""

from __future__ import annotations

from app.domain.jobs import Job
from app.domain.outreach import Contact, HeuristicOutreachDrafter
from app.domain.tailoring import TailoredMaterials
from app.integrations.mock.research import MockResearchClient

_MATERIALS = TailoredMaterials(
    summary="Fit for Staff Backend Engineer at Ledgerline.",
    highlights=(
        "Re-architected the settlement pipeline — cut runtime by 70%",
        "Introduced distributed tracing across services",
    ),
    cover_letter="(letter)",
)

_JOB = Job(
    source="mock",
    external_id="job-1001",
    title="Staff Backend Engineer, Payments",
    company="Ledgerline",
    description="Settlement workers over Kafka.",
)

_CONTACT = Contact(
    name="Priya Raman", source="careers page", title="Engineering Manager", email="p@example.com"
)


def test_draft_addresses_researched_contact_by_name() -> None:
    message = HeuristicOutreachDrafter().draft(_MATERIALS, _JOB, _CONTACT)
    assert "Hi Priya Raman," in message.body
    assert _JOB.title in message.subject


def test_draft_without_contact_addresses_hiring_team_generically() -> None:
    message = HeuristicOutreachDrafter().draft(_MATERIALS, _JOB, None)
    assert "Hello Ledgerline hiring team," in message.body
    assert "Priya" not in message.body  # no invented recipient


def test_draft_quotes_material_highlights_verbatim() -> None:
    message = HeuristicOutreachDrafter().draft(_MATERIALS, _JOB, _CONTACT)
    for highlight in _MATERIALS.highlights:
        assert highlight in message.body


async def test_mock_research_finds_fixture_contact(
    mock_research_client: MockResearchClient,
) -> None:
    contact = await mock_research_client.find_contact(_JOB)
    assert contact is not None
    assert contact.name == "Priya Raman"
    assert contact.source  # provenance always recorded


async def test_mock_research_returns_none_for_unknown_company(
    mock_research_client: MockResearchClient,
) -> None:
    unknown = Job(
        source="mock", external_id="x", title="Engineer", company="Northwind", description=""
    )
    assert await mock_research_client.find_contact(unknown) is None
