"""Ad-platform integration routes: OAuth connect/callback, account discovery,
enable/disable, disconnect.

The OAuth callback arrives from the ad platform with no auth header, so the team
is carried in a short-lived signed `state` token (JWT, 15-min expiry) created at
connect time and verified on callback.
"""

import logging
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.api.v1.deps import CurrentUser, get_current_user
from app.config import APP_BASE_URL, JWT_ALGORITHM, JWT_SECRET
from app.db import ad_accounts as accounts_db
from app.responses import success_response
from app.services.ads.base import ConnectorAuthError, ConnectorConfigError, ConnectorError
from app.services.ads.registry import connector_status, get_connector

logger = logging.getLogger(__name__)
router = APIRouter()

_STATE_TTL = 900  # 15 minutes
_FRONTEND_REDIRECT = f"{APP_BASE_URL}/dashboard/integrations"


# ── State signing ─────────────────────────────────────────────────────────────

def _sign_state(team_id: str, user_id: str, platform: str) -> str:
    payload = {
        "team_id": team_id,
        "user_id": user_id,
        "platform": platform,
        "nonce": uuid.uuid4().hex,
        "exp": int(time.time()) + _STATE_TTL,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _verify_state(state: str, platform: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=400, detail={"message": "Invalid OAuth state", "code": "BAD_STATE"}) from e
    if payload.get("platform") != platform:
        raise HTTPException(status_code=400, detail={"message": "State/platform mismatch", "code": "BAD_STATE"})
    return payload


def _require_known_platform(platform: str):
    try:
        return get_connector(platform)
    except ConnectorConfigError as e:
        raise HTTPException(status_code=404, detail={"message": str(e), "code": "UNKNOWN_PLATFORM"}) from e


# ── Status / list ─────────────────────────────────────────────────────────────

@router.get("")
def list_integrations(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """All ad platforms, their config status, and this team's connection state."""
    connections = {c["platform"]: c for c in accounts_db.list_connections(current_user.team_id)}
    out = []
    for status in connector_status():
        conn = connections.get(status["platform"])
        accounts = accounts_db.list_ad_accounts(current_user.team_id, status["platform"]) if conn else []
        out.append({
            **status,
            "connected": conn is not None,
            "connection_status": (conn or {}).get("status"),
            "last_error": (conn or {}).get("last_error"),
            "connected_at": (conn or {}).get("connected_at"),
            "account_count": len(accounts),
            "enabled_account_count": len([a for a in accounts if a.get("enabled", True)]),
        })
    return success_response(out)


# ── Connect (returns the consent URL) ─────────────────────────────────────────

@router.get("/{platform}/connect")
def connect(
    platform: str,
    redirect: bool = Query(False, description="302 to the consent page instead of returning JSON"),
    current_user: CurrentUser = Depends(get_current_user),
):
    connector = _require_known_platform(platform)
    if not connector.is_configured:
        raise HTTPException(
            status_code=400,
            detail={"message": f"{connector.label} is not configured on the server.", "code": "NOT_CONFIGURED"},
        )
    state = _sign_state(current_user.team_id, current_user.user_id, platform)
    url = connector.authorize_url(state)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return success_response({"authorize_url": url})


# ── OAuth callback (no auth header — trust the signed state) ───────────────────

@router.get("/{platform}/callback")
def callback(
    platform: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error or not code or not state:
        return _redirect_back(platform, "error", error or "missing_code")

    payload = _verify_state(state, platform)
    team_id, user_id = payload["team_id"], payload["user_id"]
    connector = _require_known_platform(platform)

    try:
        token = connector.exchange_code(code)
        accounts_db.save_connection(
            team_id, platform, token, connected_by=user_id, scopes=token.scope
        )
        # Discover ad accounts immediately so the user sees them right away.
        try:
            for info in connector.list_ad_accounts(token):
                accounts_db.upsert_ad_account(team_id, info)
        except ConnectorError:
            logger.exception("account discovery failed for %s after connect", platform)
    except ConnectorAuthError as e:
        return _redirect_back(platform, "error", f"auth:{e}")
    except ConnectorError as e:
        logger.exception("OAuth exchange failed for %s", platform)
        return _redirect_back(platform, "error", str(e))

    return _redirect_back(platform, "success")


def _redirect_back(platform: str, status: str, reason: str | None = None) -> RedirectResponse:
    params = {"connected": platform, "status": status}
    if reason:
        params["reason"] = reason[:120]
    return RedirectResponse(f"{_FRONTEND_REDIRECT}?{urlencode(params)}", status_code=302)


# ── Accounts under a connection ───────────────────────────────────────────────

@router.get("/{platform}/accounts")
def list_accounts(platform: str, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_known_platform(platform)
    accounts = accounts_db.list_ad_accounts(current_user.team_id, platform)
    return success_response([{k: v for k, v in a.items() if k not in ("pk", "sk")} for a in accounts])


@router.post("/{platform}/accounts/refresh")
def refresh_accounts(platform: str, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Re-discover ad accounts from the platform (e.g. after the user gains access to a new one)."""
    connector = _require_known_platform(platform)
    token = accounts_db.load_token(current_user.team_id, platform)
    if not token:
        raise HTTPException(status_code=400, detail={"message": "Not connected.", "code": "NOT_CONNECTED"})
    try:
        infos = connector.list_ad_accounts(token)
    except ConnectorAuthError as e:
        accounts_db.mark_connection_error(current_user.team_id, platform, str(e))
        raise HTTPException(status_code=400, detail={"message": "Reconnect required.", "code": "AUTH_EXPIRED"}) from e
    except ConnectorError as e:
        raise HTTPException(status_code=502, detail={"message": str(e), "code": "PLATFORM_ERROR"}) from e
    for info in infos:
        accounts_db.upsert_ad_account(current_user.team_id, info)
    return success_response({"discovered": len(infos)}, "Accounts refreshed")


@router.patch("/{platform}/accounts/{account_id}")
def toggle_account(
    platform: str,
    account_id: str,
    enabled: bool = Query(..., description="Include this account in syncs/dashboards"),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_known_platform(platform)
    updated = accounts_db.set_account_enabled(current_user.team_id, platform, account_id, enabled)
    if not updated:
        raise HTTPException(status_code=404, detail={"message": "Account not found.", "code": "NOT_FOUND"})
    return success_response({k: v for k, v in updated.items() if k not in ("pk", "sk")}, "Updated")


# ── Disconnect ────────────────────────────────────────────────────────────────

@router.delete("/{platform}")
def disconnect(platform: str, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_known_platform(platform)
    accounts_db.disconnect(current_user.team_id, platform)
    return success_response({"platform": platform}, "Disconnected")
