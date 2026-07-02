"""Tests for token encryption at rest."""

from __future__ import annotations

import pytest
from app.security.crypto import TokenCipher, TokenEncryptionError


def test_round_trip() -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    token = "ya29.super-secret-oauth-token"
    encrypted = cipher.encrypt(token)
    assert encrypted != token
    assert cipher.decrypt(encrypted) == token


def test_ciphertext_does_not_contain_plaintext() -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    assert "secret" not in cipher.encrypt("secret-value")


def test_empty_key_raises() -> None:
    with pytest.raises(TokenEncryptionError):
        TokenCipher("")


def test_invalid_key_raises() -> None:
    with pytest.raises(TokenEncryptionError):
        TokenCipher("not-a-valid-fernet-key")


def test_wrong_key_cannot_decrypt() -> None:
    encrypted = TokenCipher(TokenCipher.generate_key()).encrypt("secret")
    other = TokenCipher(TokenCipher.generate_key())
    with pytest.raises(TokenEncryptionError):
        other.decrypt(encrypted)


def test_tampered_ciphertext_fails_authentication() -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    encrypted = cipher.encrypt("secret")
    tampered = encrypted[:-2] + ("AA" if encrypted[-2:] != "AA" else "BB")
    with pytest.raises(TokenEncryptionError):
        cipher.decrypt(tampered)
