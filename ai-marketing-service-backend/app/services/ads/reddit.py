"""Reddit Ads connector — Reddit Ads API v3 + Reddit OAuth2.

Targets:
  - OAuth authorize: https://www.reddit.com/api/v1/authorize
  - Token endpoint:  https://www.reddit.com/api/v1/access_token  (HTTP Basic auth)
  - Ads base:        https://ads-api.reddit.com/api/v3  (Bearer auth)
  - Accounts:        GET  {base}/ad_accounts
  - Campaigns:       GET  {base}/ad_accounts/{id}/campaigns
  - Reporting:       POST {base}/ad_accounts/{id}/reports

A ``User-Agent`` header (REDDIT_USER_AGENT) is required on every Reddit request or
calls are throttled/blocked. Reddit reports `spend` in microcurrency on some
endpoints; values that look like micros (very large) are divided by 1e6 — see
``_norm_spend``. The exact account-membership path can vary by API tier, so
``list_ad_accounts`` uses the documented ``GET /ad_accounts`` and notes the caveat.
"""

from __future__ import annotations

import time

import httpx

from app.config import (
    REDDIT_ADS_CLIENT_ID,
    REDDIT_ADS_CLIENT_SECRET,
    REDDIT_ADS_REDIRECT_URI,
    REDDIT_USER_AGENT,
)
from app.services.ads.base import (
    AdConnector,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    TokenBundle,
)
from app.services.ads.schema import AdAccountInfo, AdInsight, Campaign

_AUTH_URL = "https://www.reddit.com/api/v1/authorize"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API = "https://ads-api.reddit.com/api/v3"


class RedditAdsConnector(AdConnector):
    key = "reddit_ads"
    label = "Reddit Ads"
    supports_management = False  # read-only phase; flip on when write methods land

    @property
    def is_configured(self) -> bool:
        return bool(
            REDDIT_ADS_CLIENT_ID and REDDIT_ADS_CLIENT_SECRET and REDDIT_ADS_REDIRECT_URI
        )

    def _require_config(self) -> None:
        if not self.is_configured:
            raise ConnectorConfigError("Reddit Ads app credentials are not configured")

    # ── OAuth ─────────────────────────────────────────────────────────────────

    def authorize_url(self, state: str, redirect_uri: str | None = None) -> str:
        self._require_config()
        params = {
            "client_id": REDDIT_ADS_CLIENT_ID,
            "response_type": "code",
            "state": state,
            "redirect_uri": redirect_uri or REDDIT_ADS_REDIRECT_URI,
            "duration": "permanent",  # request a refresh token
            "scope": "adsread adsedit",
        }
        from urllib.parse import urlencode

        return f"{_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> TokenBundle:
        self._require_config()
        # Authorization-code grant; client credentials go in HTTP Basic auth.
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri or REDDIT_ADS_REDIRECT_URI,
            }
        )

    def refresh(self, refresh_token: str) -> TokenBundle:
        self._require_config()
        bundle = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        if not bundle.refresh_token:
            bundle.refresh_token = refresh_token
        return bundle

    def _token_request(self, data: dict) -> TokenBundle:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    _TOKEN_URL,
                    data=data,
                    auth=(REDDIT_ADS_CLIENT_ID, REDDIT_ADS_CLIENT_SECRET),
                    headers={"User-Agent": REDDIT_USER_AGENT},
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Reddit token request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ConnectorAuthError(f"Reddit token rejected ({resp.status_code})")
        body = _safe_json(resp)
        if resp.status_code >= 400 or body.get("error"):
            raise ConnectorError(f"Reddit token error ({resp.status_code}): {body}")
        access = body.get("access_token")
        if not access:
            raise ConnectorError("Reddit token response missing access_token")
        expires_in = body.get("expires_in")
        expires_at = int(time.time()) + int(expires_in) if expires_in else None
        return TokenBundle(
            access_token=access,
            refresh_token=body.get("refresh_token"),
            expires_at=expires_at,
            scope=body.get("scope"),
            token_type=body.get("token_type", "Bearer"),
            raw=body,
        )

    # ── Data ──────────────────────────────────────────────────────────────────

    def _headers(self, token: TokenBundle) -> dict:
        return {
            "Authorization": f"Bearer {token.access_token}",
            "User-Agent": REDDIT_USER_AGENT,
            "Content-Type": "application/json",
        }

    def list_ad_accounts(self, token: TokenBundle) -> list[AdAccountInfo]:
        with httpx.Client(timeout=30) as client:
            # Documented account listing; some API tiers require a business-scoped
            # path (GET /businesses/{id}/ad_accounts) — adjust if `data` is empty.
            body = self._get(client, token, f"{_API}/ad_accounts")
        out: list[AdAccountInfo] = []
        for row in _data_list(body):
            out.append(
                AdAccountInfo(
                    platform=self.key,
                    account_id=str(row.get("id", "")),
                    name=row.get("name", ""),
                    currency=row.get("currency", "USD"),
                    status=str(row.get("status", "active")).lower(),
                    timezone=row.get("time_zone_id") or row.get("timezone"),
                )
            )
        return out

    def fetch_campaigns(self, token: TokenBundle, account_id: str) -> list[Campaign]:
        with httpx.Client(timeout=30) as client:
            # Campaigns belonging to one ad account.
            body = self._get(client, token, f"{_API}/ad_accounts/{account_id}/campaigns")
        out: list[Campaign] = []
        for row in _data_list(body):
            out.append(
                Campaign(
                    platform=self.key,
                    account_id=account_id,
                    campaign_id=str(row.get("id", "")),
                    name=row.get("name", ""),
                    status=str(row.get("configured_status", "unknown")).lower(),
                    objective=row.get("objective"),
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
        payload = {
            "data": {
                "breakdowns": ["campaign_id", "date"],
                "fields": [
                    "spend",
                    "impressions",
                    "clicks",
                    "conversions",
                    "conversion_value",
                ],
                "starts_at": f"{since}T00:00:00Z",
                "ends_at": f"{until}T23:59:59Z",
                "time_zone_id": "GMT",
                "group_by": ["campaign_id", "date"],
            }
        }
        with httpx.Client(timeout=30) as client:
            # Reporting endpoint; rows keyed by campaign_id + date breakdown.
            body = self._post(
                client, token, f"{_API}/ad_accounts/{account_id}/reports", payload
            )
        out: list[AdInsight] = []
        for row in _data_list(body):
            # The breakdown keys may live at the row root or under a nested dict.
            date = str(row.get("date") or row.get("date_start") or since)[:10]
            out.append(
                AdInsight(
                    platform=self.key,
                    account_id=account_id,
                    level=level,
                    entity_id=str(row.get("campaign_id", "")),
                    entity_name=row.get("campaign_name", ""),
                    date=date,
                    spend=_norm_spend(row.get("spend")),
                    impressions=_to_int(row.get("impressions")),
                    clicks=_to_int(row.get("clicks")),
                    conversions=_to_float(row.get("conversions")),
                    conversion_value=_to_float(row.get("conversion_value")),
                )
            )
        return out

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, client: httpx.Client, token: TokenBundle, url: str) -> dict:
        try:
            resp = client.get(url, headers=self._headers(token))
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Reddit request failed: {exc}") from exc
        return self._parse(resp)

    def _post(
        self, client: httpx.Client, token: TokenBundle, url: str, payload: dict
    ) -> dict:
        try:
            resp = client.post(url, headers=self._headers(token), json=payload)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Reddit request failed: {exc}") from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict:
        if resp.status_code in (401, 403):
            raise ConnectorAuthError(f"Reddit Ads auth error ({resp.status_code})")
        body = _safe_json(resp)
        if resp.status_code >= 400:
            raise ConnectorError(f"Reddit Ads API error ({resp.status_code}): {body}")
        return body if isinstance(body, dict) else {}


# ── Module-level utilities ─────────────────────────────────────────────────────

def _data_list(body: dict) -> list[dict]:
    """Extract the `data` list defensively (Reddit nests under `data` or `data.metrics`)."""
    data = body.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("metrics", "rows", "data"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _norm_spend(value) -> float:
    """Reddit returns spend in microcurrency on some endpoints; large values are
    treated as micros and scaled down, otherwise taken as major units."""
    amount = _to_float(value)
    # Heuristic: a single-campaign daily spend above 100k almost certainly means micros.
    if amount >= 100_000:
        return round(amount / 1_000_000.0, 6)
    return amount


def _safe_json(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {"data": body}


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
