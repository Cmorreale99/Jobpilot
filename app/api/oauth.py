"""OAuth authorization routes: begin consent and handle the provider callback.

    GET /oauth/{provider}/start?user_id=...   -> 302 redirect to the consent screen
    GET /oauth/{provider}/callback?code&state -> exchange + encrypted store, JSON result

The ``state`` minted in ``/start`` is validated (single-use) in ``/callback``, so the
handshake is CSRF-protected and replay-resistant.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.api.deps import get_flow
from app.integrations.oauth.base import OAuthError
from app.services.oauth_flow import OAuthFlowService

router = APIRouter(prefix="/oauth", tags=["oauth"])

FlowDep = Annotated[OAuthFlowService, Depends(get_flow)]


@router.get("/{provider}/start")
def start(
    provider: str,
    flow: FlowDep,
    user_id: Annotated[str, Query(description="The JobPilot user connecting the account.")],
) -> RedirectResponse:
    """Begin authorization: redirect the user to the provider's consent screen."""
    try:
        request = flow.start(provider, user_id)
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(request.url, status_code=302)


@router.get("/{provider}/callback")
def callback(
    provider: str,
    flow: FlowDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> dict[str, str]:
    """Handle the provider redirect: exchange the code and store the credential."""
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth provider returned: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing 'code' or 'state'.")
    try:
        credential = flow.complete(state, code)
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if credential.provider != provider:
        raise HTTPException(status_code=400, detail="Provider mismatch for this callback.")
    return {
        "status": "connected",
        "provider": credential.provider,
        "user_id": credential.user_id,
        "account_label": credential.account_label,
    }
