"""SQLAlchemy-backed :class:`OAuthCredentialStore` — the production persistence.

Encrypts tokens with a :class:`TokenCipher` before they touch the database and decrypts
on read, so the ``oauth_credentials`` table only ever holds ciphertext.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import OAuthCredentialRow
from app.domain.credentials import OAuthCredential
from app.security.crypto import TokenCipher


class SqlOAuthCredentialStore:
    """Persistent credential store over a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session], cipher: TokenCipher) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def get(self, user_id: str, provider: str) -> OAuthCredential | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(OAuthCredentialRow).where(
                    OAuthCredentialRow.user_id == user_id,
                    OAuthCredentialRow.provider == provider,
                )
            )
            if row is None:
                return None
            return OAuthCredential(
                user_id=row.user_id,
                provider=row.provider,
                account_label=row.account_label,
                access_token=self._cipher.decrypt(row.encrypted_access_token),
                refresh_token=(
                    self._cipher.decrypt(row.encrypted_refresh_token)
                    if row.encrypted_refresh_token
                    else None
                ),
                scopes=tuple(row.scopes.split()) if row.scopes else (),
                expires_at=row.expires_at,
            )

    def upsert(self, credential: OAuthCredential) -> None:
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(OAuthCredentialRow).where(
                    OAuthCredentialRow.user_id == credential.user_id,
                    OAuthCredentialRow.provider == credential.provider,
                )
            )
            if row is None:
                row = OAuthCredentialRow(user_id=credential.user_id, provider=credential.provider)
                session.add(row)
            row.account_label = credential.account_label
            row.encrypted_access_token = self._cipher.encrypt(credential.access_token)
            row.encrypted_refresh_token = (
                self._cipher.encrypt(credential.refresh_token) if credential.refresh_token else None
            )
            row.scopes = " ".join(credential.scopes) or None
            row.expires_at = credential.expires_at

    def delete(self, user_id: str, provider: str) -> None:
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(OAuthCredentialRow).where(
                    OAuthCredentialRow.user_id == user_id,
                    OAuthCredentialRow.provider == provider,
                )
            )
            if row is not None:
                session.delete(row)
