"""TikTok Ads connector — TikTok Business / Marketing API v1.3.

Targets:
  - OAuth portal:   https://business-api.tiktok.com/portal/auth
  - Base:           https://business-api.tiktok.com/open_api/v1.3
  - Token:          POST {base}/oauth2/access_token/
  - Advertisers:    GET  {base}/oauth2/advertiser/get/  +  GET {base}/advertiser/info/
  - Campaigns:      GET  {base}/campaign/get/
  - Reporting:      GET  {base}/report/integrated/get/

Every business-API response is wrapped as ``{"code":0,"message":"OK","data":{...}}``;
a non-zero ``code`` is an error (auth codes 40001/40105/40100/etc → reconnect).
Tokens are long-lived with NO refresh. Auth is sent in the ``Access-Token`` header.
List-typed query params are JSON-encoded strings, per TikTok's convention.
"""

from __future__ import annotations

import json

import httpx

from app.config import (
    TIKTOK_ADS_APP_ID,
    TIKTOK_ADS_REDIRECT_URI,
    TIKTOK_ADS_SECRET,
)
from app.services.ads.base import (
    AdConnector,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    TokenBundle,
)
from app.services.ads.schema import AdAccountInfo, AdInsight, Campaign

_BASE = "https://business-api.tiktok.com/open_api/v1.3"
# Business-API error codes that indicate an invalid/expired token → reconnect.
_AUTH_CODES = {40001, 40100, 40101, 40102, 40104, 40105, 40110}


class TikTokAdsConnector(AdConnector):
    key = "tiktok_ads"
    label = "TikTok Ads"
    supports_management = False  # read-only phase; flip on when write methods land

    @property
    def is_configured(self) -> bool:
        return bool(TIKTOK_ADS_APP_ID and TIKTOK_ADS_SECRET and TIKTOK_ADS_REDIRECT_URI)

    def _require_config(self) -> None:
        if not self.is_configured:
            raise ConnectorConfigError("TikTok Ads app credentials are not configured")

    # ── OAuth ─────────────────────────────────────────────────────────────────

    def authorize_url(self, state: str, redirect_uri: str | None = None) -> str:
        self._require_config()
        params = {
            "app_id": TIKTOK_ADS_APP_ID,
            "redirect_uri": redirect_uri or TIKTOK_ADS_REDIRECT_URI,
            "state": state,
        }
        from urllib.parse import urlencode

        return f"https://business-api.tiktok.com/portal/auth?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> TokenBundle:
        self._require_config()
        try:
            with httpx.Client(timeout=30) as client:
                # auth_code → long-lived access token (+ list of advertiser_ids).
                resp = client.post(
                    f"{_BASE}/oauth2/access_token/",
                    json={
                        "app_id": TIKTOK_ADS_APP_ID,
                        "secret": TIKTOK_ADS_SECRET,
                        "auth_code": code,
                    },
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"TikTok token exchange failed: {exc}") from exc
        data = self._unwrap(resp)
        access = data.get("access_token")
        if not access:
            raise ConnectorError("TikTok token exchange returned no access_token")
        return TokenBundle(
            access_token=access,
            refresh_token=None,  # TikTok has no refresh token
            expires_at=None,  # long-lived
            scope=",".join(str(s) for s in (data.get("scope") or [])) or None,
            raw=data,
        )

    def refresh(self, refresh_token: str) -> TokenBundle:
        # TikTok access tokens are long-lived and cannot be refreshed; the caller
        # treats this as "needs reconnect". Fail gracefully rather than networking.
        raise ConnectorError("TikTok Ads tokens do not refresh; reconnect required")

    # ── Data ──────────────────────────────────────────────────────────────────

    def list_ad_accounts(self, token: TokenBundle) -> list[AdAccountInfo]:
        with httpx.Client(timeout=30) as client:
            # Advertiser ids authorized for this app+token.
            data = self._get(
                client,
                token,
                f"{_BASE}/oauth2/advertiser/get/",
                params={
                    "app_id": TIKTOK_ADS_APP_ID,
                    "secret": TIKTOK_ADS_SECRET,
                    "access_token": token.access_token,
                },
            )
            advertisers = data.get("list", []) or []
            ids = [str(a.get("advertiser_id")) for a in advertisers if a.get("advertiser_id")]
            if not ids:
                return []
            # Hydrate names/currency/status/timezone for the discovered advertisers.
            info = self._get(
                client,
                token,
                f"{_BASE}/advertiser/info/",
                params={
                    "advertiser_ids": json.dumps(ids),
                    "fields": json.dumps(["name", "currency", "status", "timezone"]),
                },
            )
        out: list[AdAccountInfo] = []
        for row in info.get("list", []) or []:
            status_raw = str(row.get("status", "")).upper()
            status = "active" if status_raw in ("STATUS_ENABLE", "ENABLE", "") else "disabled"
            out.append(
                AdAccountInfo(
                    platform=self.key,
                    account_id=str(row.get("advertiser_id", "")),
                    name=row.get("name", ""),
                    currency=row.get("currency", "USD"),
                    status=status,
                    timezone=row.get("timezone"),
                )
            )
        return out

    def fetch_campaigns(self, token: TokenBundle, account_id: str) -> list[Campaign]:
        with httpx.Client(timeout=30) as client:
            # Campaign list for one advertiser (single page; sized generously).
            data = self._get(
                client,
                token,
                f"{_BASE}/campaign/get/",
                params={"advertiser_id": account_id, "page_size": 1000},
            )
        out: list[Campaign] = []
        for row in data.get("list", []) or []:
            status_raw = str(row.get("operation_status", "")).upper()
            status = "active" if status_raw in ("ENABLE", "STATUS_ENABLE") else "paused"
            out.append(
                Campaign(
                    platform=self.key,
                    account_id=account_id,
                    campaign_id=str(row.get("campaign_id", "")),
                    name=row.get("campaign_name", ""),
                    status=status,
                    objective=row.get("objective_type"),
                    daily_budget=_to_float_or_none(row.get("budget")),
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
            "advertiser_id": account_id,
            "report_type": "BASIC",
            "data_level": "AUCTION_CAMPAIGN",
            "dimensions": json.dumps(["campaign_id", "stat_time_day"]),
            "metrics": json.dumps(
                [
                    "spend",
                    "impressions",
                    "clicks",
                    "conversion",
                    "total_complete_payment_rate",
                    "video_play_actions",
                ]
            ),
            "start_date": since,
            "end_date": until,
            "page_size": 1000,
        }
        with httpx.Client(timeout=30) as client:
            # Integrated BASIC report, daily granularity, one row per campaign/day.
            data = self._get(client, token, f"{_BASE}/report/integrated/get/", params)
        out: list[AdInsight] = []
        for row in data.get("list", []) or []:
            dims = row.get("dimensions", {}) or {}
            metrics = row.get("metrics", {}) or {}
            stat_day = str(dims.get("stat_time_day", since))
            out.append(
                AdInsight(
                    platform=self.key,
                    account_id=account_id,
                    level=level,
                    entity_id=str(dims.get("campaign_id", "")),
                    entity_name=metrics.get("campaign_name", ""),
                    date=stat_day[:10],  # take the date part of "YYYY-MM-DD HH:MM:SS"
                    spend=_to_float(metrics.get("spend")),
                    impressions=_to_int(metrics.get("impressions")),
                    clicks=_to_int(metrics.get("clicks")),
                    conversions=_to_float(metrics.get("conversion")),
                    video_views=_to_int(metrics.get("video_play_actions")),
                )
            )
        return out

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(
        self, client: httpx.Client, token: TokenBundle, url: str, params: dict
    ) -> dict:
        try:
            resp = client.get(url, params=params, headers={"Access-Token": token.access_token})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"TikTok request failed: {exc}") from exc
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        """Validate the `{code,message,data}` envelope and return `data`."""
        if resp.status_code in (401, 403):
            raise ConnectorAuthError(f"TikTok auth error (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ConnectorError(f"TikTok returned non-JSON (HTTP {resp.status_code})") from exc
        code = body.get("code")
        if code in _AUTH_CODES:
            raise ConnectorAuthError(f"TikTok token invalid (code {code}): {body.get('message')}")
        if code not in (0, None):
            raise ConnectorError(f"TikTok API error (code {code}): {body.get('message')}")
        if resp.status_code >= 400:
            raise ConnectorError(f"TikTok API error (HTTP {resp.status_code}): {body}")
        data = body.get("data")
        return data if isinstance(data, dict) else {}


# ── Module-level utilities ─────────────────────────────────────────────────────

def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_float_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
