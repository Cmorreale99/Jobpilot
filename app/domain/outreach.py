"""Outreach drafting: contacts, messages, and the drafter protocol — pure domain.

An outreach draft is written *about* tailored materials, *to* a researched contact.
Two truthfulness rules are structural here:

* **Contacts are researched, never invented.** A :class:`Contact` only exists if a
  ``ResearchClient`` found one; with ``contact=None`` the draft addresses the hiring
  team generically. No implementation may fabricate a name.
* **Evidence comes from the materials.** The heuristic quotes highlights verbatim; the
  LLM-backed drafter (``app/llm/drafting.py``) receives only real materials to work from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.jobs import Job
from app.domain.tailoring import TailoredMaterials

# How many evidence highlights an outreach body quotes (outreach is shorter than a letter).
_OUTREACH_HIGHLIGHTS = 3


@dataclass(frozen=True)
class Contact:
    """A researched outreach contact (e.g. a hiring manager or recruiter).

    ``source`` records where the contact was found, so every recipient is auditable —
    the same provenance rule the Master CV follows.
    """

    name: str
    source: str
    title: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class OutreachMessage:
    """A drafted outreach message, ready for the approval queue."""

    subject: str
    body: str


class OutreachDrafter(Protocol):
    """Drafts one outreach message from materials + job + optional researched contact."""

    def draft(
        self, materials: TailoredMaterials, job: Job, contact: Contact | None
    ) -> OutreachMessage: ...


class HeuristicOutreachDrafter:
    """Deterministic template drafter — the mock-first default."""

    def draft(
        self, materials: TailoredMaterials, job: Job, contact: Contact | None
    ) -> OutreachMessage:
        greeting = f"Hi {contact.name}," if contact else f"Hello {job.company} hiring team,"
        highlights = materials.highlights[:_OUTREACH_HIGHLIGHTS]
        lines = [
            greeting,
            "",
            f"I'm reaching out about the {job.title} opening at {job.company}. "
            "A few directly relevant results from my background:",
            *[f"- {h}" for h in highlights],
            "",
            "I'd appreciate the chance to talk about the role. Thank you for your time.",
        ]
        return OutreachMessage(
            subject=f"Regarding the {job.title} role at {job.company}",
            body="\n".join(lines),
        )
