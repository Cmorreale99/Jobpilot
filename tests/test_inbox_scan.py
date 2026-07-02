"""Inbox scanning: mock query scoping, offline Gmail mapping, factory, credentials."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from app.config import Settings
from app.domain.credentials import PROVIDER_GDRIVE, OAuthCredential
from app.integrations.base import InboxConfigurationError, MailConfigurationError, MailCredentials
from app.integrations.inbox_factory import create_inbox_scanner
from app.integrations.mock.inbox import MockInboxScanner
from app.integrations.real.gmail_inbox import GmailInboxScanner
from app.security.crypto import TokenCipher
from app.services.credentials import InMemoryOAuthCredentialStore, resolve_inbox_credentials

from tests.conftest import INBOX_FIXTURES_DIR

_QUERY = 'interview OR "phone screen" OR "schedule a call"'


# --- mock ------------------------------------------------------------------------------


async def test_mock_scanner_returns_only_query_matching_mail() -> None:
    scanner = MockInboxScanner(INBOX_FIXTURES_DIR)
    messages = await scanner.search_messages(_QUERY)
    ids = {m.message_id for m in messages}
    assert "msg-2001" in ids  # invite
    assert "msg-2002" in ids  # phone screen
    assert "msg-2004" not in ids  # job digest never enters scope


async def test_mock_scanner_honors_since() -> None:
    scanner = MockInboxScanner(INBOX_FIXTURES_DIR)
    messages = await scanner.search_messages(_QUERY, since=datetime(2026, 6, 30, tzinfo=UTC))
    assert {m.message_id for m in messages} == {"msg-2001"}


# --- Gmail (offline via MockTransport) ---------------------------------------------------

_LIST_PAYLOAD = {"messages": [{"id": "gm-1"}]}
_DETAIL_PAYLOAD = {
    "id": "gm-1",
    "snippet": "We'd like to schedule a 60-minute interview next week.",
    "internalDate": "1782828300000",
    "payload": {
        "headers": [
            {"name": "Subject", "value": "Interview invitation - Data Engineer"},
            {"name": "From", "value": "recruiting@ledgerline.example"},
        ]
    },
}


async def test_gmail_scanner_maps_messages_and_scopes_query() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            seen["q"] = request.url.params["q"]
            seen["auth"] = request.headers.get("Authorization", "")
            return httpx.Response(200, json=_LIST_PAYLOAD)
        return httpx.Response(200, json=_DETAIL_PAYLOAD)

    scanner = GmailInboxScanner(
        MailCredentials(sender_email="jordan@example.com", access_token="tok"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://gmail.test/gmail/v1/users/me",
    )
    messages = await scanner.search_messages(_QUERY, since=datetime(2026, 6, 30, tzinfo=UTC))

    assert seen["q"].startswith(_QUERY)
    assert "after:" in seen["q"]  # the window rides in the scoped query itself
    assert seen["auth"] == "Bearer tok"
    (message,) = messages
    assert message.message_id == "gm-1"
    assert message.subject == "Interview invitation - Data Engineer"
    assert message.sender == "recruiting@ledgerline.example"
    assert "schedule a 60-minute interview" in message.body
    assert message.received_at is not None and message.received_at.year == 2026


async def test_gmail_scanner_empty_inbox() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    scanner = GmailInboxScanner(
        MailCredentials(sender_email="j@example.com", access_token="tok"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://gmail.test/gmail/v1/users/me",
    )
    assert await scanner.search_messages(_QUERY) == []


# --- credentials + factory ----------------------------------------------------------------


def _store_with(scopes: tuple[str, ...]) -> InMemoryOAuthCredentialStore:
    store = InMemoryOAuthCredentialStore(TokenCipher(TokenCipher.generate_key()))
    store.upsert(
        OAuthCredential(
            user_id="u1",
            provider=PROVIDER_GDRIVE,
            account_label="jordan@example.com",
            access_token="tok",
            scopes=scopes,
        )
    )
    return store


def test_resolve_inbox_credentials_requires_readonly_scope() -> None:
    store = _store_with(("https://www.googleapis.com/auth/gmail.send",))
    with pytest.raises(MailConfigurationError, match="gmail.readonly"):
        resolve_inbox_credentials(store, "u1")


def test_resolve_inbox_credentials_happy_path() -> None:
    store = _store_with(("https://www.googleapis.com/auth/gmail.readonly",))
    creds = resolve_inbox_credentials(store, "u1")
    assert creds.access_token == "tok"


def test_factory_defaults_to_mock(settings: Settings) -> None:
    assert isinstance(create_inbox_scanner(settings), MockInboxScanner)


def test_factory_gmail_without_store_fails_clearly() -> None:
    with pytest.raises(InboxConfigurationError, match="credential store"):
        create_inbox_scanner(Settings(gmail_enabled=True))
