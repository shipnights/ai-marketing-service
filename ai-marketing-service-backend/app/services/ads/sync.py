"""Ad insights sync — pull normalized metrics from each connected platform into
the analytics store. Invoked manually (route) or on a schedule (cron/worker).

Token lifecycle: before fetching, refresh expired access tokens and persist the
refreshed bundle. Auth failures mark the connection so the UI can prompt a reconnect.
"""

import logging
from datetime import UTC, datetime, timedelta

from app.config import ADS_SYNC_LOOKBACK_DAYS
from app.db import ad_accounts as accounts_db
from app.db import ad_stats as stats_db
from app.services.ads.base import AdConnector, ConnectorAuthError, ConnectorError, TokenBundle
from app.services.ads.registry import get_connector

logger = logging.getLogger(__name__)


def date_range(lookback_days: int | None = None) -> tuple[str, str]:
    days = lookback_days if lookback_days is not None else ADS_SYNC_LOOKBACK_DAYS
    today = datetime.now(UTC).date()
    since = today - timedelta(days=max(0, days - 1))
    return since.isoformat(), today.isoformat()


def _fresh_token(team_id: str, platform: str, connector: AdConnector, token: TokenBundle) -> TokenBundle:
    """Refresh and persist the token if it's expired and refreshable."""
    if not token.is_expired():
        return token
    if not token.refresh_token:
        return token  # long-lived, no refresh — let the call fail → reconnect prompt
    refreshed = connector.refresh(token.refresh_token)
    if not refreshed.refresh_token:
        refreshed.refresh_token = token.refresh_token  # some platforms omit it on refresh
    accounts_db.store_refreshed_token(team_id, platform, refreshed)
    return refreshed


def sync_account(
    team_id: str, platform: str, account_id: str, since: str, until: str
) -> dict:
    """Sync one ad account. Returns a per-account result dict."""
    connector = get_connector(platform)
    token = accounts_db.load_token(team_id, platform)
    if not token:
        return {"platform": platform, "account_id": account_id, "error": "not_connected"}
    try:
        token = _fresh_token(team_id, platform, connector, token)
        insights = connector.fetch_insights(token, account_id, since, until, level="campaign")
        stats_db.store_insights(team_id, insights)
        accounts_db.mark_account_synced(team_id, platform, account_id)
        return {"platform": platform, "account_id": account_id, "rows": len(insights)}
    except ConnectorAuthError as e:
        accounts_db.mark_connection_error(team_id, platform, str(e))
        return {"platform": platform, "account_id": account_id, "error": "auth", "detail": str(e)}
    except ConnectorError as e:
        logger.warning("sync failed %s/%s: %s", platform, account_id, e)
        return {"platform": platform, "account_id": account_id, "error": "fetch", "detail": str(e)}


def sync_team(team_id: str, *, lookback_days: int | None = None, platform: str | None = None) -> dict:
    """Sync all enabled ad accounts for a team (optionally one platform). Returns a summary."""
    since, until = date_range(lookback_days)
    accounts = accounts_db.list_ad_accounts(team_id, platform)
    results = []
    total_rows = 0
    for acct in accounts:
        if not acct.get("enabled", True):
            continue
        r = sync_account(team_id, acct["platform"], acct["account_id"], since, until)
        results.append(r)
        total_rows += r.get("rows", 0)
    return {
        "team_id": team_id,
        "since": since,
        "until": until,
        "accounts_synced": len([r for r in results if "rows" in r]),
        "rows": total_rows,
        "results": results,
    }
