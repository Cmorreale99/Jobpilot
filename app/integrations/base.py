"""Integration interfaces and their data contracts.

Only the ``DriveClient`` surface is defined here so far. The interface is deliberately
**narrow**: it exposes exactly what Master CV ingestion needs — discover candidate
career artifacts, read their extracted text, and inspect metadata. It intentionally does
NOT expose delete, move, permission mutation, or unrestricted write. Drive is an
evidence source, not a filesystem we manage.

``domain/`` never imports this module's implementations; it operates on plain data
handed to it by the service layer, so it stays decoupled from both mocks and MCP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.interviews import InboxMessage
from app.domain.jobs import Job
from app.domain.outreach import Contact


class DriveConfigurationError(RuntimeError):
    """Raised when a Drive client is asked to run without valid configuration.

    Used by the MCP-backed client to fail loudly (rather than silently doing nothing)
    when it is enabled but missing a server endpoint or credentials.
    """


class DriveResponseError(RuntimeError):
    """Raised when an MCP Drive tool returns a payload we can't map to our contract."""


@dataclass(frozen=True)
class DriveCredentials:
    """Per-user Google Drive access, resolved from the encrypted token store.

    ``user_email`` identifies the Google account (the ``user_google_email`` argument the
    Workspace MCP tools require). ``access_token`` is a short-lived OAuth token; it is
    never logged and never persisted in plaintext (decrypted on read from
    ``oauth_credentials``).
    """

    user_email: str
    access_token: str


@dataclass(frozen=True)
class DriveSource:
    """A candidate career artifact discovered in Drive, before its body is read.

    ``source_ref`` is the opaque handle passed back to :meth:`DriveClient.read_source`
    (a Drive file id for real integrations, a fixture id for the mock).
    """

    source_ref: str
    title: str
    mime_type: str
    folder_id: str | None = None
    modified_time: datetime | None = None


@dataclass(frozen=True)
class DriveSourceMetadata:
    """Metadata about a source without its extracted text."""

    source_ref: str
    title: str
    mime_type: str
    folder_id: str | None = None
    modified_time: datetime | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class DriveDocument:
    """A source with its extracted plain-text body.

    ``text`` is already-extracted text (PDF/DOCX/Doc conversion happens inside the
    client), ready to be handed to the Master CV builder / LLM structurer.
    """

    source_ref: str
    title: str
    mime_type: str
    text: str
    folder_id: str | None = None
    modified_time: datetime | None = None


@runtime_checkable
class DriveClient(Protocol):
    """Read-only, career-artifact-scoped view of Google Drive.

    Every implementation (mock or MCP-backed) honors the same contract so the pipeline
    is agnostic to where evidence comes from. The interface is async because the real
    client talks to an MCP server over the network; the mock is trivially async.
    """

    async def list_candidate_sources(self, user_id: str) -> list[DriveSource]:
        """List candidate career artifacts for a user.

        Implementations return raw candidates; source *policy* (folder scoping and MIME
        allowlisting) is applied by the service layer so the rule lives in one place.
        """
        ...

    async def read_source(self, source_ref: str) -> DriveDocument:
        """Read and return the extracted text of a single source."""
        ...

    async def get_source_metadata(self, source_ref: str) -> DriveSourceMetadata:
        """Return metadata for a single source without reading its body."""
        ...

    async def list_changed_sources(self, user_id: str, since: datetime) -> list[DriveSource]:
        """List candidate sources modified since ``since`` (for incremental refresh)."""
        ...


# =================================================================================
# GitHub
# =================================================================================


class GitHubConfigurationError(RuntimeError):
    """Raised when a GitHub client is asked to run without valid configuration."""


class GitHubResponseError(RuntimeError):
    """Raised when a GitHub MCP tool returns a payload we can't map to our contract."""


@dataclass(frozen=True)
class GitHubCredentials:
    """GitHub access for the MCP server: a personal access token (never logged).

    Decrypted on read from ``oauth_credentials``; forwarded to the GitHub MCP server.
    """

    access_token: str


@dataclass(frozen=True)
class GitHubRepo:
    """A candidate repository, before its README/signals are read.

    ``repo_ref`` is ``"owner/name"`` — the handle passed to :meth:`GitHubClient.read_repo`.
    """

    repo_ref: str
    name: str
    owner: str
    description: str | None = None
    primary_language: str | None = None
    is_fork: bool = False
    is_private: bool = False
    stars: int = 0
    pushed_at: datetime | None = None


@dataclass(frozen=True)
class GitHubRepoMetadata:
    """Contribution signals for a repository (no README body)."""

    repo_ref: str
    name: str
    owner: str
    primary_language: str | None = None
    languages: tuple[str, ...] = ()
    stars: int = 0
    forks: int = 0
    commit_count: int | None = None
    pushed_at: datetime | None = None


@dataclass(frozen=True)
class GitHubDocument:
    """A repository's extracted README text (career evidence)."""

    repo_ref: str
    title: str
    text: str
    primary_language: str | None = None
    pushed_at: datetime | None = None


@dataclass(frozen=True)
class GitHubCommit:
    """One commit message from the user's repository (career evidence).

    Commit messages are legal Result evidence under the V2 content gate — what is
    validated is whether the text is an outcome statement, not where it came from.
    ``sha`` is the evidence ``source_ref``.
    """

    repo_ref: str
    sha: str
    message: str
    authored_at: datetime | None = None


@runtime_checkable
class GitHubClient(Protocol):
    """Read-only, career-evidence view of GitHub.

    Narrow by design: list the user's repositories, read a repo's README, and fetch
    contribution signals. No issue/PR/write operations. Async because the real client
    talks to the GitHub MCP server; the mock is trivially async.
    """

    async def list_candidate_repos(self, user_id: str) -> list[GitHubRepo]:
        """List candidate repositories. Repo *policy* (owner scope, fork/private
        exclusion) is applied by the service layer so the rule lives in one place."""
        ...

    async def read_repo(self, repo_ref: str) -> GitHubDocument:
        """Read and return the README text of a single repository."""
        ...

    async def list_commits(self, repo_ref: str) -> list[GitHubCommit]:
        """List commit messages for a repository (read-only career evidence).

        Fuels V2 claim extraction: commit messages are searched for work statements
        (Action evidence) and outcome statements (Result evidence). Still no
        issue/PR/write operations.
        """
        ...

    async def get_repo_metadata(self, repo_ref: str) -> GitHubRepoMetadata:
        """Return contribution signals for a single repository."""
        ...

    async def list_changed_repos(self, user_id: str, since: datetime) -> list[GitHubRepo]:
        """List repositories pushed to since ``since`` (for incremental refresh)."""
        ...


# =================================================================================
# Jobs
# =================================================================================


@runtime_checkable
class JobSource(Protocol):
    """A source of job postings (compliant API, mock fixtures, ...).

    Returns the domain :class:`Job` type directly — jobs are a first-class domain entity
    (persisted and matched), so unlike Drive/GitHub there is no separate transport shape.
    """

    async def fetch_recent_jobs(self, since: datetime | None = None) -> list[Job]:
        """Fetch postings, optionally only those posted after ``since``."""
        ...


# =================================================================================
# Uploads (local career artifacts)
# =================================================================================


class UploadsConfigurationError(RuntimeError):
    """Raised when an uploads client points at a directory that does not exist."""


@dataclass(frozen=True)
class UploadCandidate:
    """A file discovered in the uploads directory, before its body is read.

    ``upload_ref`` is the file name relative to the uploads directory — the handle
    passed to :meth:`UploadsClient.read_upload`.
    """

    upload_ref: str
    title: str
    mime_type: str
    modified_time: datetime | None = None


@dataclass(frozen=True)
class UploadDocument:
    """An uploaded file with its extracted plain-text body."""

    upload_ref: str
    title: str
    mime_type: str
    text: str
    modified_time: datetime | None = None


@runtime_checkable
class UploadsClient(Protocol):
    """Read-only view of user-supplied career artifacts (a local uploads directory).

    Same narrow contract as Drive/GitHub: discover candidates, read text. Async for
    interface symmetry even though the default implementation is local disk.
    """

    async def list_candidate_uploads(self) -> list[UploadCandidate]:
        """List candidate files. Suffix *policy* is applied by the service layer."""
        ...

    async def read_upload(self, upload_ref: str) -> UploadDocument:
        """Read and return the text of a single uploaded file."""
        ...


# =================================================================================
# Mail (outreach sending — always behind approval)
# =================================================================================


class MailConfigurationError(RuntimeError):
    """Raised when a mail client is asked to send without valid configuration."""


class MailSendError(RuntimeError):
    """Raised when a send attempt fails (the draft stays approved for retry)."""


@dataclass(frozen=True)
class MailCredentials:
    """Sending identity resolved from the encrypted token store (never logged)."""

    sender_email: str
    access_token: str


@dataclass(frozen=True)
class OutgoingEmail:
    """One approved outreach message, ready to send."""

    to: str
    subject: str
    body: str


@runtime_checkable
class MailClient(Protocol):
    """Sends one email. Deliberately the narrowest possible surface — no reading,
    no drafts, no threads. Inbox *reading* is a separate, flag-gated concern (M7)."""

    async def send_email(self, email: OutgoingEmail) -> str:
        """Send and return the provider's message id."""
        ...


# =================================================================================
# Inbox scan (interview detection — the ONLY permitted inbox read)
# =================================================================================


class InboxConfigurationError(RuntimeError):
    """Raised when an inbox scanner is asked to run without valid configuration."""


class InboxResponseError(RuntimeError):
    """Raised when an inbox API returns a payload we can't map to our contract."""


@runtime_checkable
class InboxScanner(Protocol):
    """Query-scoped, read-only inbox search for interview detection.

    Deliberately the narrowest possible surface: one search method, no labels, no
    threads, no send, no modify. The *query* keeps reads scoped to likely interview
    mail (data minimization); the ``INTERVIEW_INBOX_SCAN`` flag gates whether it is
    ever called at all.
    """

    async def search_messages(
        self, query: str, since: datetime | None = None
    ) -> list[InboxMessage]:
        """Return messages matching ``query`` (optionally received after ``since``)."""
        ...

    async def get_message(self, message_id: str) -> InboxMessage | None:
        """Re-fetch one message by its provider id, or ``None`` if it does not exist.

        The V2 anti-hallucination gate: before an interview is recorded, its evidence
        quote is verified against THIS re-fetched body — never against the copy the
        detector already had.
        """
        ...


# =================================================================================
# Contact research (outreach)
# =================================================================================


@runtime_checkable
class ResearchClient(Protocol):
    """Finds a real outreach contact for a job posting — or honestly returns none.

    Narrow by design: one lookup, read-only. Returning ``None`` is the correct answer
    when no contact can be verified; downstream drafting then addresses the hiring team
    generically. Implementations must never fabricate a person.
    """

    async def find_contact(self, job: Job) -> Contact | None:
        """Return a researched contact for ``job``'s company/role, or ``None``."""
        ...
