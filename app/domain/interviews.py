"""Interviews: invite detection, stage state machine, prep packets — pure domain.

The interview scan turns scoped inbox messages into :class:`Interview` rows and prep
packets. The same truthfulness rules as the rest of the domain apply:

* **Detection is conservative.** The heuristic detector only flags messages with
  explicit invite language; ambiguous mail is ignored (a missed invite is recoverable,
  a hallucinated one pollutes the dashboard).
* **Prep packets derive from real data** — the application's tailored materials and the
  Master CV's claims. Neither generator invents experience.
* ``interviews.stage`` is an explicit state machine with validated transitions
  (:func:`~app.domain.applications.validate_transition`), like the other lifecycles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.domain.applications import Application, validate_transition
from app.domain.cv import MasterCv
from app.domain.jobs import Job, JobMatch
from app.domain.tailoring import render_claim, select_claims


@dataclass(frozen=True)
class InboxMessage:
    """One email surfaced by a scoped inbox search (a first-class domain shape, like
    :class:`~app.domain.jobs.Job` — integrations return it directly).

    ``message_id`` is the provider's stable id (the interview dedupe key). ``body`` is
    plain text, truncated by the client — enough for invite detection, never archival.
    """

    message_id: str
    subject: str
    sender: str
    body: str
    received_at: datetime | None = None


class InterviewStage(StrEnum):
    """Lifecycle of one detected interview (``interviews.stage``).

    V2 inserts ``confirmed`` between detection and scheduling: a detected interview
    sits in the dashboard **confirm queue** until a human confirms it is real. Prep
    packets are generated only for confirmed interviews — same philosophy as claims
    (deterministic provenance check, then a human queue).
    """

    DETECTED = "detected"
    CONFIRMED = "confirmed"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# detected -> confirmed -> scheduled (-> completed); cancellable at any live stage.
# Terminal stages cannot reopen — a new invite email creates a new interview. There is
# deliberately NO detected -> scheduled shortcut: nothing skips the confirm queue.
INTERVIEW_TRANSITIONS: dict[InterviewStage, frozenset[InterviewStage]] = {
    InterviewStage.DETECTED: frozenset({InterviewStage.CONFIRMED, InterviewStage.CANCELLED}),
    InterviewStage.CONFIRMED: frozenset({InterviewStage.SCHEDULED, InterviewStage.CANCELLED}),
    InterviewStage.SCHEDULED: frozenset({InterviewStage.COMPLETED, InterviewStage.CANCELLED}),
    InterviewStage.COMPLETED: frozenset(),
    InterviewStage.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class InterviewInvite:
    """A detected invite: what the detector could truthfully extract, nothing more.

    ``evidence_quote`` is the verbatim trigger text from the message **body** — the
    provenance that verification re-checks against the re-fetched message before any
    record is created. A detection without a body quote is not a detection.
    """

    company: str
    evidence_quote: str
    job_title: str | None = None


@dataclass(frozen=True)
class Interview:
    """One detected interview, deduped on ``(user_id, gmail_message_id)``.

    Hard provenance: ``gmail_message_id`` and ``evidence_quote`` are required —
    no message id, no record.
    """

    id: int
    user_id: str
    gmail_message_id: str
    company: str
    stage: InterviewStage
    evidence_quote: str = ""
    job_title: str | None = None
    received_at: datetime | None = None


@dataclass(frozen=True)
class PrepPacket:
    """The prep material generated for one interview (markdown content)."""

    interview_id: int
    content: str


class InviteDetector(Protocol):
    """Decides whether one inbox message is an interview invite."""

    def detect(self, message: InboxMessage) -> InterviewInvite | None: ...


class PrepPacketGenerator(Protocol):
    """Builds a prep packet from the interview + the user's real evidence."""

    def generate(
        self,
        interview: Interview,
        master_cv: MasterCv,
        application: Application | None,
    ) -> PrepPacket: ...


# --- heuristic invite detection ------------------------------------------------------

# Explicit invite language only; rejections and job-board noise contain none of these.
_INVITE_PATTERNS = (
    "interview",
    "phone screen",
    "schedule a call",
    "meet the team",
    "next round",
)
_REJECTION_PATTERNS = ("other candidates", "not to move forward", "unfortunately")

_TITLE_RES = (
    re.compile(r"for the (?P<title>[^.\n]+?) role", re.IGNORECASE),
    re.compile(r"application for (?P<title>[^.\n]+?) at ", re.IGNORECASE),
)
_SENDER_DOMAIN_RE = re.compile(r"@(?P<domain>[a-z0-9.-]+)", re.IGNORECASE)


def _extract_company(sender: str) -> str:
    match = _SENDER_DOMAIN_RE.search(sender)
    if not match:
        return sender.strip() or "Unknown"
    label = match.group("domain").split(".")[0]
    return label.capitalize()


def _extract_title(message: InboxMessage) -> str | None:
    for pattern in _TITLE_RES:
        for text in (message.body, message.subject):
            match = pattern.search(text)
            if match:
                title = match.group("title").strip()
                if 3 <= len(title) <= 80:
                    return title
    return None


def _extract_evidence_quote(body: str) -> str | None:
    """The verbatim line of the BODY containing the first invite pattern.

    The quote must come from the body (not the subject) because verification asserts
    it against the re-fetched message body. Being a line of the original text, it is
    a verbatim substring by construction.
    """
    for line in body.splitlines():
        lowered = line.lower()
        if any(pattern in lowered for pattern in _INVITE_PATTERNS):
            stripped = line.strip()
            if stripped:
                return stripped
    return None


class HeuristicInviteDetector:
    """Keyword-based invite detection — conservative, deterministic, offline.

    V2: a message whose invite language appears only in the subject is NOT detected —
    without a body quote there is nothing verification could re-check, and a missed
    invite is recoverable while a hallucinated one is not.
    """

    def detect(self, message: InboxMessage) -> InterviewInvite | None:
        haystack = f"{message.subject} {message.body}".lower()
        if not any(pattern in haystack for pattern in _INVITE_PATTERNS):
            return None
        if any(pattern in haystack for pattern in _REJECTION_PATTERNS):
            return None
        evidence_quote = _extract_evidence_quote(message.body)
        if evidence_quote is None:
            return None
        return InterviewInvite(
            company=_extract_company(message.sender),
            evidence_quote=evidence_quote,
            job_title=_extract_title(message),
        )


# --- provenance verification (the anti-hallucination gate) ----------------------------


def verify_invite_provenance(
    gmail_message_id: str,
    evidence_quote: str,
    fetched: InboxMessage | None,
) -> str | None:
    """Deterministic pre-insert check; returns the failure reason, or ``None`` to pass.

    Hard provenance: a missing message id or empty quote fails before any fetch is
    even consulted. The quote must appear verbatim (whitespace-reflow tolerant, same
    rule as the PAR validator) in the **re-fetched** message body — the detector's
    own copy of the message proves nothing.
    """
    from app.domain.par_validation import verbatim_in

    if not gmail_message_id.strip():
        return "no gmail_message_id: an interview without a message id cannot exist"
    if not evidence_quote.strip():
        return "no evidence_quote: an interview without verbatim trigger text cannot exist"
    if fetched is None:
        return f"message {gmail_message_id!r} could not be re-fetched from the provider"
    if fetched.message_id != gmail_message_id:
        return (
            f"re-fetch returned message {fetched.message_id!r}, not the cited {gmail_message_id!r}"
        )
    if not verbatim_in(evidence_quote, fetched.body):
        return "evidence_quote is not a substring of the re-fetched message body"
    return None


def normalize_company(name: str) -> str:
    """Comparison key for company names ('Sentinel Pay' ~ 'sentinelpay.example')."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


# --- heuristic prep packet ------------------------------------------------------------


class HeuristicPrepPacketGenerator:
    """Deterministic markdown prep packet built from real materials and claims."""

    def generate(
        self,
        interview: Interview,
        master_cv: MasterCv,
        application: Application | None,
    ) -> PrepPacket:
        role = interview.job_title or (application.job_title if application else "the role")
        lines = [f"# Interview prep — {role} at {interview.company}", ""]

        if application is not None:
            lines += ["## Your application", application.materials.summary, ""]
            highlights = application.materials.highlights
        else:
            highlights = tuple(render_claim(c) for c in master_cv.claims[:5])
        if highlights:
            lines += ["## Evidence to lead with", *[f"- {h}" for h in highlights], ""]

        if application is not None and master_cv.claims:
            # Broaden beyond the application's highlights with the CV's own top claims.
            job = Job(
                source=application.job_source,
                external_id=application.job_external_id,
                title=application.job_title,
                company=application.job_company,
                description=application.materials.summary,
            )
            match = JobMatch(job=job, score=0.0, rank=0, rationale="")
            extra = [
                render_claim(c)
                for c in select_claims(master_cv, match, limit=3)
                if render_claim(c) not in highlights
            ]
            if extra:
                lines += ["## Backup stories", *[f"- {e}" for e in extra], ""]

        lines += [
            "## Questions to ask",
            "- What does success in this role look like after 90 days?",
            "- What is the team's biggest technical challenge right now?",
            "- How does the team review, ship, and roll back changes?",
            "",
            "## Logistics",
            f"- Detected from message {interview.gmail_message_id}"
            + (f" (received {interview.received_at:%Y-%m-%d})" if interview.received_at else ""),
            "- Confirm the time zone and who you will be speaking with.",
        ]
        return PrepPacket(interview_id=interview.id, content="\n".join(lines))


# --- persistence contract --------------------------------------------------------------


@runtime_checkable
class InterviewRepository(Protocol):
    """Persistence for interviews and their prep packets.

    ``upsert_interview`` is **idempotent** per ``(user_id, gmail_message_id)``: a
    re-scan returns the existing row (stage untouched — never regressed to
    ``detected``) with ``created=False``. Prep packets keep one row per interview.
    """

    def upsert_interview(
        self,
        user_id: str,
        gmail_message_id: str,
        invite: InterviewInvite,
        received_at: datetime | None,
    ) -> tuple[Interview, bool]: ...

    def get_interview(self, interview_id: int) -> Interview | None: ...

    def list_interviews(self, user_id: str) -> list[Interview]: ...

    def transition_interview(self, interview_id: int, new_stage: InterviewStage) -> Interview: ...

    def save_prep_packet(self, packet: PrepPacket) -> None: ...

    def get_prep_packet(self, interview_id: int) -> PrepPacket | None: ...


def validate_interview_transition(current: InterviewStage, new: InterviewStage) -> None:
    """Raise :class:`~app.domain.applications.InvalidTransitionError` on illegal changes."""
    validate_transition(INTERVIEW_TRANSITIONS, current, new)
