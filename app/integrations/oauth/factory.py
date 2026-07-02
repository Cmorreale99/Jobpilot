"""Builds the default set of real OAuth providers from config."""

from __future__ import annotations

import httpx

from app.config import Settings, get_settings
from app.domain.credentials import PROVIDER_GDRIVE, PROVIDER_GITHUB
from app.integrations.oauth.base import OAuthProvider
from app.integrations.oauth.github import GitHubOAuthProvider
from app.integrations.oauth.google import GoogleOAuthProvider


def create_oauth_providers(
    settings: Settings | None = None, *, client: httpx.Client | None = None
) -> dict[str, OAuthProvider]:
    """Return the real Google + GitHub providers keyed by provider id."""
    settings = settings or get_settings()
    return {
        PROVIDER_GDRIVE: GoogleOAuthProvider(settings, client),
        PROVIDER_GITHUB: GitHubOAuthProvider(settings, client),
    }
