"""Mail clients: mock outbox, offline Gmail mapping, credential resolution, factory."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from app.config import Settings
from app.domain.credentials import PROVIDER_GDRIVE, OAuthCredential
from app.integrations.base import (
    MailConfigurationError,
    MailCredentials,
    MailSendError,
    OutgoingEmail,
)
from app.integrations.mail_factory import create_mail_client
from app.integrations.mock.mail import MockMailClient
from app.integrations.real.gmail import GmailMailClient
from app.security.crypto import TokenCipher
from app.services.credentials import InMemoryOAuthCredentialStore, resolve_mail_credentials

_EMAIL = OutgoingEmail(to="priya@example.com", subject="Regarding the role", body="Hi Priya,")
_CREDS = MailCredentials(sender_email="jordan@example.com", access_token="tok")


# --- mock ------------------------------------------------------------------------------


async def test_mock_client_records_to_outbox() -> None:
    client = MockMailClient()
    message_id = await client.send_email(_EMAIL)
    assert message_id == "mock-msg-1"
    assert client.outbox == [_EMAIL]


async def test_mock_client_scripted_failure() -> None:
    client = MockMailClient(fail_times=1)
    with pytest.raises(MailSendError):
        await client.send_email(_EMAIL)
    assert await client.send_email(_EMAIL) == "mock-msg-1"  # recovers


# --- Gmail (offline via MockTransport) ---------------------------------------------------


def _gmail(handler: httpx.MockTransport) -> GmailMailClient:
    return GmailMailClient(
        _CREDS,
        httpx.AsyncClient(transport=handler),
        send_url="https://gmail.test/send",
    )


async def test_gmail_sends_rfc822_with_bearer_token() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["raw"] = json.loads(request.content)["raw"]
        return httpx.Response(200, json={"id": "gmail-123"})

    message_id = await _gmail(httpx.MockTransport(handler)).send_email(_EMAIL)

    assert message_id == "gmail-123"
    assert seen["auth"] == "Bearer tok"
    decoded = base64.urlsafe_b64decode(str(seen["raw"])).decode()
    assert "To: priya@example.com" in decoded
    assert "From: jordan@example.com" in decoded
    assert "Subject: Regarding the role" in decoded
    assert "Hi Priya," in decoded


async def test_gmail_http_error_raises_mail_send_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "insufficient scope"})

    with pytest.raises(MailSendError):
        await _gmail(httpx.MockTransport(handler)).send_email(_EMAIL)


async def test_gmail_missing_message_id_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with pytest.raises(MailSendError):
        await _gmail(httpx.MockTransport(handler)).send_email(_EMAIL)


# --- credential resolution ----------------------------------------------------------------


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


def test_resolve_mail_credentials_happy_path() -> None:
    store = _store_with(("https://www.googleapis.com/auth/gmail.send",))
    creds = resolve_mail_credentials(store, "u1")
    assert creds == _CREDS


def test_resolve_mail_credentials_missing_credential() -> None:
    store = InMemoryOAuthCredentialStore(TokenCipher(TokenCipher.generate_key()))
    with pytest.raises(MailConfigurationError, match="No stored Google credential"):
        resolve_mail_credentials(store, "u1")


def test_resolve_mail_credentials_missing_scope() -> None:
    store = _store_with(("https://www.googleapis.com/auth/drive.readonly",))
    with pytest.raises(MailConfigurationError, match="gmail.send scope"):
        resolve_mail_credentials(store, "u1")


# --- factory -------------------------------------------------------------------------------


def test_factory_defaults_to_mock() -> None:
    assert isinstance(create_mail_client(Settings(gmail_enabled=False)), MockMailClient)


def test_factory_gmail_without_store_fails_clearly() -> None:
    with pytest.raises(MailConfigurationError, match="credential store"):
        create_mail_client(Settings(gmail_enabled=True))


def test_factory_gmail_with_store_returns_real_client() -> None:
    store = _store_with(("https://www.googleapis.com/auth/gmail.send",))
    client = create_mail_client(Settings(gmail_enabled=True), store=store, user_id="u1")
    assert isinstance(client, GmailMailClient)
