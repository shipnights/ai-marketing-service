"""Ad reporting routes — unified, cross-platform stats for connected ad accounts.

Read-only metrics (Phase A). Campaign management (create/pause/budget) lands in a
later phase behind the connector `supports_management` flag.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.deps import CurrentUser, get_current_user
from app.db import ad_accounts as accounts_db
from app.db import ad_stats as stats_db
from app.responses import success_response
from app.services.ads import sync as sync_service
from app.services.ads.registry import get_connector
from app.services.ads.base import ConnectorError

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_RANGE_DAYS = 366
_VALID_SORTS = {"spend", "impressions", "clicks", "conversions", "conversion_value", "roas", "ctr", "cpc"}


def _resolve_range(since: str | None, until: str | None) -> tuple[str, str]:
    """Default to the trailing 30 days; validate YYYY-MM-DD and ordering."""
    today = datetime.now(UTC).date()
    try:
        until_d = datetime.strptime(until, "%Y-%m-%d").date() if until else today
        since_d = datetime.strptime(since, "%Y-%m-%d").date() if since else (until_d - timedelta(days=29))
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"message": "Dates must be YYYY-MM-DD.", "code": "BAD_DATE"}) from e
    if since_d > until_d:
        raise HTTPException(status_code=422, detail={"message": "`since` is after `until`.", "code": "BAD_RANGE"})
    if (until_d - since_d).days > _MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail={"message": "Range too large (max 366 days).", "code": "BAD_RANGE"})
    return since_d.isoformat(), until_d.isoformat()


# ── Connected accounts ────────────────────────────────────────────────────────

@router.get("/accounts")
def list_ad_accounts(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Every connected ad account across all platforms."""
    accounts = accounts_db.list_ad_accounts(current_user.team_id)
    return success_response([{k: v for k, v in a.items() if k not in ("pk", "sk")} for a in accounts])


# ── Unified reporting ─────────────────────────────────────────────────────────

@router.get("/overview")
def overview(
    since: str | None = None,
    until: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Grand totals, per-platform breakdown, and a daily time series for the window."""
    s, u = _resolve_range(since, until)
    return success_response(stats_db.summary(current_user.team_id, s, u))


@router.get("/campaigns")
def campaigns(
    since: str | None = None,
    until: str | None = None,
    sort_by: str = "spend",
    limit: int = Query(20, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Top campaigns across platforms for the window, aggregated and ranked."""
    if sort_by not in _VALID_SORTS:
        raise HTTPException(status_code=422, detail={"message": f"sort_by must be one of {sorted(_VALID_SORTS)}", "code": "BAD_SORT"})
    s, u = _resolve_range(since, until)
    return success_response(stats_db.top_campaigns(current_user.team_id, s, u, sort_by=sort_by, limit=limit))


@router.get("/insights")
def insights(
    since: str | None = None,
    until: str | None = None,
    platform: str | None = None,
    account_id: str | None = None,
    level: str = "campaign",
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Raw insight rows (filterable) for tables and custom charts."""
    s, u = _resolve_range(since, until)
    rows = stats_db.query_insights(
        current_user.team_id, s, u, platform=platform, account_id=account_id, level=level
    )
    return success_response({"since": s, "until": u, "count": len(rows), "rows": rows})


# ── Live campaign list (straight from the platform, not the cache) ────────────

@router.get("/{platform}/{account_id}/campaigns/live")
def live_campaigns(
    platform: str,
    account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch the current campaign list directly from the platform (status/budgets)."""
    connector = get_connector(platform)
    token = accounts_db.load_token(current_user.team_id, platform)
    if not token:
        raise HTTPException(status_code=400, detail={"message": "Not connected.", "code": "NOT_CONNECTED"})
    try:
        items = connector.fetch_campaigns(token, account_id)
    except ConnectorError as e:
        raise HTTPException(status_code=502, detail={"message": str(e), "code": "PLATFORM_ERROR"}) from e
    return success_response([c.to_dict() for c in items])


# ── Sync ──────────────────────────────────────────────────────────────────────

@router.post("/sync")
def trigger_sync(
    platform: str | None = None,
    lookback_days: int | None = Query(None, ge=1, le=366),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Pull fresh insights for the team's enabled ad accounts.

    Runs inline (fine for a manual 'Sync now' over a handful of accounts); the same
    `sync_team` is what a scheduled worker calls.
    """
    result = sync_service.sync_team(current_user.team_id, lookback_days=lookback_days, platform=platform)
    return success_response(result, "Sync complete")
