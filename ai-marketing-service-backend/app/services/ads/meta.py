"""Meta (Facebook) Ads connector — Marketing API over the Graph API.

Targets the Graph API at version ``META_GRAPH_VERSION`` (default v21.0):
  - OAuth dialog:   https://www.facebook.com/{version}/dialog/oauth
  - Token exchange: GET {base}/oauth/access_token
                    GET {base}/oauth/access_token?grant_type=fb_exchange_token
  - Accounts:       GET {base}/me/adaccounts
  - Campaigns:      GET {base}/{act_id}/campaigns
  - Insights:       GET {base}/{act_id}/insights (level=campaign, time_increment=1)

Meta access tokens are long-lived (~60 days) and have NO refresh token; the
long-lived token is re-exchanged via the ``fb_exchange_token`` grant to extend it.
Money fields from campaigns are in minor units (cents) → divided by 100; insight
``spend`` is already returned by the API in major currency units.
"""

from __future__ import annotations

import json
import time

import httpx

from app.config import (
    META_ADS_REDIRECT_URI,
    META_APP_ID,
    META_APP_SECRET,
    META_GRAPH_VERSION,
)
from app.services.ads.base import (
    AdConnector,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    TokenBundle,
)
from app.services.ads.schema import AdAccountInfo, AdInsight, Campaign

# Conversion-like action types we count toward `conversions` / `conversion_value`.
_CONVERSION_ACTIONS = {
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "lead",
    "complete_registration",
}


class MetaAdsConnector(AdConnector):
    key = "meta_ads"
    label = "Meta Ads"
    supports_management = False  # read-only phase; flip on when write methods land

    @property
    def _base(self) -> str:
        return f"https://graph.facebook.com/{META_GRAPH_VERSION}"

    @property
    def is_configured(self) -> bool:
        return bool(META_APP_ID and META_APP_SECRET and META_ADS_REDIRECT_URI)

    def _require_config(self) -> None:
        if not self.is_configured:
            raise ConnectorConfigError("Meta Ads app credentials are not configured")

    # ── OAuth ─────────────────────────────────────────────────────────────────

    def authorize_url(self, state: str, redirect_uri: str | None = None) -> str:
        self._require_config()
        params = {
            "client_id": META_APP_ID,
            "redirect_uri": redirect_uri or META_ADS_REDIRECT_URI,
            "state": state,
            # read-first product, but request management so the later phase works
            "scope": "ads_read,ads_management,business_management",
            "response_type": "code",
        }
        return f"https://www.facebook.com/{META_GRAPH_VERSION}/dialog/oauth?{_urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> TokenBundle:
        self._require_config()
        try:
            with httpx.Client(timeout=30) as client:
                # Step 1: code → short-lived user access token.
                short = self._get_json(
                    client,
                    f"{self._base}/oauth/access_token",
                    params={
                        "client_id": META_APP_ID,
                        "client_secret": META_APP_SECRET,
                        "redirect_uri": redirect_uri or META_ADS_REDIRECT_URI,
                        "code": code,
                    },
                )
                short_token = short.get("access_token")
                if not short_token:
                    raise ConnectorError("Meta token exchange returned no access_token")
                # Step 2: short-lived → long-lived token (~60 days).
                return self._exchange_long_lived(client, short_token)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Meta token exchange failed: {exc}") from exc

    def refresh(self, refresh_token: str) -> TokenBundle:
        # Meta has no refresh token: treat the stored long-lived token as input
        # and re-exchange it via fb_exchange_token to extend its lifetime.
        self._require_config()
        try:
            with httpx.Client(timeout=30) as client:
                return self._exchange_long_lived(client, refresh_token)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Meta token refresh failed: {exc}") from exc

    def _exchange_long_lived(self, client: httpx.Client, token: str) -> TokenBundle:
        data = self._get_json(
            client,
            f"{self._base}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": META_APP_ID,
                "client_secret": META_APP_SECRET,
                "fb_exchange_token": token,
            },
        )
        access = data.get("access_token")
        if not access:
            raise ConnectorError("Meta long-lived exchange returned no access_token")
        expires_in = data.get("expires_in")
        expires_at = int(time.time()) + int(expires_in) if expires_in else None
        # Meta returns no refresh_token; keep the long-lived token as the refresh seed.
        return TokenBundle(
            access_token=access,
            refresh_token=access,
            expires_at=expires_at,
            token_type="Bearer",
            raw=data,
        )

    # ── Data ──────────────────────────────────────────────────────────────────

    def list_ad_accounts(self, token: TokenBundle) -> list[AdAccountInfo]:
        with httpx.Client(timeout=30) as client:
            # Ad accounts the user administers; `id` is the act_<id> form used by insights.
            rows = self._get_paged(
                client,
                token,
                f"{self._base}/me/adaccounts",
                params={"fields": "account_id,name,currency,account_status,timezone_name"},
            )
        out: list[AdAccountInfo] = []
        for r in rows:
            status = "active" if r.get("account_status") == 1 else "disabled"
            out.append(
                AdAccountInfo(
                    platform=self.key,
                    account_id=r.get("id") or f"act_{r.get('account_id')}",
                    name=r.get("name") or r.get("id", ""),
                    currency=r.get("currency", "USD"),
                    status=status,
                    timezone=r.get("timezone_name"),
                )
            )
        return out

    def fetch_campaigns(self, token: TokenBundle, account_id: str) -> list[Campaign]:
        with httpx.Client(timeout=30) as client:
            # Campaigns under the ad account; budgets come back in minor units (cents).
            rows = self._get_paged(
                client,
                token,
                f"{self._base}/{account_id}/campaigns",
                params={
                    "fields": "id,name,status,objective,daily_budget,"
                    "lifetime_budget,start_time,stop_time"
                },
            )
        out: list[Campaign] = []
        for r in rows:
            out.append(
                Campaign(
                    platform=self.key,
                    account_id=account_id,
                    campaign_id=r.get("id", ""),
                    name=r.get("name", ""),
                    status=str(r.get("status", "unknown")).lower(),
                    objective=r.get("objective"),
                    daily_budget=_cents_to_major(r.get("daily_budget")),
                    lifetime_budget=_cents_to_major(r.get("lifetime_budget")),
                    start_date=r.get("start_time"),
                    end_date=r.get("stop_time"),
                )
            )
        return out

    def fetch_insights(
        self,
        token: TokenBundle,
        account_id: str,
        since: str,
        until: str,
        level: str = "campaign",
    ) -> list[AdInsight]:
        params = {
            "level": "campaign",
            "time_increment": 1,  # one row per day
            "time_range": json.dumps({"since": since, "until": until}),
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,"
            "reach,actions,action_values",
        }
        with httpx.Client(timeout=30) as client:
            # Daily campaign insights; paged via paging.next cursor.
            rows = self._get_paged(client, token, f"{self._base}/{account_id}/insights", params)
        out: list[AdInsight] = []
        for r in rows:
            out.append(
                AdInsight(
                    platform=self.key,
                    account_id=account_id,
                    level=level,
                    entity_id=r.get("campaign_id", ""),
                    entity_name=r.get("campaign_name", ""),
                    date=r.get("date_start", since),
                    spend=_to_float(r.get("spend")),
                    impressions=_to_int(r.get("impressions")),
                    clicks=_to_int(r.get("clicks")),
                    reach=_to_int(r.get("reach")),
                    conversions=_sum_actions(r.get("actions")),
                    conversion_value=_sum_actions(r.get("action_values")),
                )
            )
        return out

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get_paged(
        self, client: httpx.Client, token: TokenBundle, url: str, params: dict
    ) -> list[dict]:
        """Follow Graph `paging.next` cursors, accumulating `data` rows."""
        rows: list[dict] = []
        next_url: str | None = url
        next_params: dict | None = {**params, "access_token": token.access_token, "limit": 200}
        while next_url:
            payload = self._get_json(client, next_url, params=next_params)
            rows.extend(payload.get("data", []) or [])
            # paging.next is a fully-formed absolute URL with cursor + token embedded.
            next_url = (payload.get("paging") or {}).get("next")
            next_params = None
        return rows

    def _get_json(self, client: httpx.Client, url: str, params: dict | None = None) -> dict:
        try:
            resp = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Meta request failed: {exc}") from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code in (401, 403):
            raise ConnectorAuthError(f"Meta auth error ({resp.status_code}): {body}")
        if resp.status_code >= 400:
            # OAuth errors (e.g. code 190 = invalid token) surface in `error`.
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict) and err.get("code") in (190, 102, 463, 467):
                raise ConnectorAuthError(f"Meta token invalid: {err}")
            raise ConnectorError(f"Meta API error ({resp.status_code}): {body}")
        return body if isinstance(body, dict) else {}


# ── Module-level parsing utilities ─────────────────────────────────────────────

def _urlencode(params: dict) -> str:
    from urllib.parse import urlencode

    return urlencode(params)


def _sum_actions(actions) -> float:
    """Sum `value` over action rows whose action_type is a conversion type."""
    if not isinstance(actions, list):
        return 0.0
    total = 0.0
    for a in actions:
        if isinstance(a, dict) and a.get("action_type") in _CONVERSION_ACTIONS:
            total += _to_float(a.get("value"))
    return total


def _cents_to_major(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return int(value) / 100.0
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
