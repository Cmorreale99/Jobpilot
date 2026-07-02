"""Symmetric encryption for OAuth tokens at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256, authenticated) keyed by
``TOKEN_ENCRYPTION_KEY``. Ciphertext is authenticated, so tampering or decrypting with
the wrong key raises rather than returning garbage.

Generate a key for ``.env``::

    python -m app.security.crypto
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings

__all__ = ["TokenCipher", "TokenEncryptionError"]


class TokenEncryptionError(RuntimeError):
    """Raised when a cipher is misconfigured or a token fails to decrypt/authenticate."""


class TokenCipher:
    """Encrypts and decrypts secret strings with a Fernet key.

    Args:
        key: a urlsafe-base64 32-byte Fernet key (see :meth:`generate_key`).
    """

    def __init__(self, key: str) -> None:
        if not key:
            raise TokenEncryptionError(
                "TOKEN_ENCRYPTION_KEY is empty. Generate one with "
                "`python -m app.security.crypto` and set it in .env."
            )
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise TokenEncryptionError(
                "TOKEN_ENCRYPTION_KEY is not a valid Fernet key (expected urlsafe-base64, "
                "32 bytes). Generate one with `python -m app.security.crypto`."
            ) from exc

    @classmethod
    def from_settings(cls, settings: Settings) -> TokenCipher:
        return cls(settings.token_encryption_key)

    @staticmethod
    def generate_key() -> str:
        """Return a fresh Fernet key suitable for ``TOKEN_ENCRYPTION_KEY``."""
        return Fernet.generate_key().decode("utf-8")

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a secret string, returning urlsafe-base64 ciphertext."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        """Decrypt ciphertext produced by :meth:`encrypt`.

        Raises :class:`TokenEncryptionError` if the token was tampered with or was
        encrypted under a different key.
        """
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise TokenEncryptionError(
                "Failed to decrypt token: wrong TOKEN_ENCRYPTION_KEY or corrupted data."
            ) from exc


if __name__ == "__main__":  # pragma: no cover - dev convenience
    print(TokenCipher.generate_key())
