"""Google Ads connector — Google Ads REST API v17 + Google OAuth2.

Targets:
  - OAuth consent:  https://accounts.google.com/o/oauth2/v2/auth
  - Token endpoint: https://oauth2.googleapis.com/token
  - Accessible accts: GET  https://googleads.googleapis.com/v17/customers:listAccessibleCustomers
  - GAQL queries:     POST https://googleads.googleapis.com/v17/customers/{cid}/googleAds:searchStream

Every Ads API call carries `Authorization: Bearer <access>`, a `developer-token`
header, and (when an MCC is configured) a `login-customer-id` header containing
only digits. Money fields are in micros → divided by 1_000_000 for major units.
"""

from __future__ import annotations

import re
import time

import httpx

from app.config import (
    GOOGLE_ADS_CLIENT_ID,
    GOOGLE_ADS_CLIENT_SECRET,
    GOOGLE_ADS_DEVELOPER_TOKEN,
    GOOGLE_ADS_LOGIN_CUSTOMER_ID,
    GOOGLE_ADS_REDIRECT_URI,
)
from app.services.ads.base import (
    AdConnector,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    TokenBundle,
)
from app.services.ads.schema import AdAccountInfo, AdInsight, Campaign

_API = "https://googleads.googleapis.com/v17"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleAdsConnector(AdConnector):
    key = "google_ads"
    label = "Google Ads"
    supports_management = False  # read-only phase; flip on when write methods land

    @property
    def is_configured(self) -> bool:
        return bool(
            GOOGLE_ADS_CLIENT_ID
            and GOOGLE_ADS_CLIENT_SECRET
            and GOOGLE_ADS_DEVELOPER_TOKEN
            and GOOGLE_ADS_REDIRECT_URI
        )

    def _require_config(self) -> None:
        if not self.is_configured:
            raise ConnectorConfigError("Google Ads app credentials are not configured")

    # ── OAuth ─────────────────────────────────────────────────────────────────

    def authorize_url(self, state: str, redirect_uri: str | None = None) -> str:
        self._require_config()
        params = {
            "client_id": GOOGLE_ADS_CLIENT_ID,
            "redirect_uri": redirect_uri or GOOGLE_ADS_REDIRECT_URI,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/adwords",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
        from urllib.parse import urlencode

        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> TokenBundle:
        self._require_config()
        # Authorization-code grant → access + refresh tokens.
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": GOOGLE_ADS_CLIENT_ID,
                "client_secret": GOOGLE_ADS_CLIENT_SECRET,
                "redirect_uri": redirect_uri or GOOGLE_ADS_REDIRECT_URI,
            }
        )

    def refresh(self, refresh_token: str) -> TokenBundle:
        self._require_config()
        # Refresh-token grant; Google does not re-issue the refresh token, so re-attach it.
        bundle = self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": GOOGLE_ADS_CLIENT_ID,
                "client_secret": GOOGLE_ADS_CLIENT_SECRET,
            }
        )
        if not bundle.refresh_token:
            bundle.refresh_token = refresh_token
        return bundle

    def _token_request(self, data: dict) -> TokenBundle:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(_TOKEN_URL, data=data)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Google token request failed: {exc}") from exc
        body = _safe_json(resp)
        if resp.status_code in (400, 401, 403):
            # invalid_grant means the refresh token was revoked/expired → reconnect.
            if body.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise ConnectorAuthError(f"Google token rejected: {body}")
        if resp.status_code >= 400:
            raise ConnectorError(f"Google token error ({resp.status_code}): {body}")
        access = body.get("access_token")
        if not access:
            raise ConnectorError("Google token response missing access_token")
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
        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "developer-token": GOOGLE_ADS_DEVELOPER_TOKEN,
            "Content-Type": "application/json",
        }
        if GOOGLE_ADS_LOGIN_CUSTOMER_ID:
            headers["login-customer-id"] = _digits(GOOGLE_ADS_LOGIN_CUSTOMER_ID)
        return headers

    def list_ad_accounts(self, token: TokenBundle) -> list[AdAccountInfo]:
        with httpx.Client(timeout=30) as client:
            # Customer ids the authenticated user can access (resource names).
            body = self._get(
                client, token, f"{_API}/customers:listAccessibleCustomers"
            )
            resource_names = body.get("resourceNames", []) or []
            out: list[AdAccountInfo] = []
            for rn in resource_names:
                cid = rn.split("/")[-1]
                info = self._describe_customer(client, token, cid)
                out.append(info)
        return out

    def _describe_customer(
        self, client: httpx.Client, token: TokenBundle, cid: str
    ) -> AdAccountInfo:
        """Best-effort descriptive name/currency/timezone via a `customer` GAQL query."""
        query = (
            "SELECT customer.id, customer.descriptive_name, "
            "customer.currency_code, customer.time_zone FROM customer"
        )
        try:
            batches = self._search_stream(client, token, cid, query)
        except ConnectorAuthError:
            raise
        except ConnectorError:
            # A single customer may be inaccessible; fall back to a bare record.
            return AdAccountInfo(platform=self.key, account_id=cid, name=cid)
        for batch in batches:
            for row in batch.get("results", []) or []:
                cust = row.get("customer", {})
                return AdAccountInfo(
                    platform=self.key,
                    account_id=str(cust.get("id", cid)),
                    name=cust.get("descriptiveName") or cid,
                    currency=cust.get("currencyCode", "USD"),
                    status="active",
                    timezone=cust.get("timeZone"),
                )
        return AdAccountInfo(platform=self.key, account_id=cid, name=cid)

    def fetch_campaigns(self, token: TokenBundle, account_id: str) -> list[Campaign]:
        cid = _digits(account_id)
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, "
            "campaign.advertising_channel_type, campaign_budget.amount_micros FROM campaign"
        )
        with httpx.Client(timeout=30) as client:
            # GAQL stream of campaigns; budget amount is in micros.
            batches = self._search_stream(client, token, cid, query)
        out: list[Campaign] = []
        for batch in batches:
            for row in batch.get("results", []) or []:
                camp = row.get("campaign", {})
                budget = row.get("campaignBudget", {})
                out.append(
                    Campaign(
                        platform=self.key,
                        account_id=account_id,
                        campaign_id=str(camp.get("id", "")),
                        name=camp.get("name", ""),
                        status=str(camp.get("status", "unknown")).lower(),
                        objective=camp.get("advertisingChannelType"),
                        daily_budget=_micros(budget.get("amountMicros")),
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
        cid = _digits(account_id)
        query = (
            "SELECT campaign.id, campaign.name, metrics.cost_micros, "
            "metrics.impressions, metrics.clicks, metrics.conversions, "
            "metrics.conversions_value, segments.date FROM campaign "
            f"WHERE segments.date BETWEEN '{since}' AND '{until}'"
        )
        with httpx.Client(timeout=30) as client:
            # Daily per-campaign metrics; one row per campaign per segments.date.
            batches = self._search_stream(client, token, cid, query)
        out: list[AdInsight] = []
        for batch in batches:
            for row in batch.get("results", []) or []:
                camp = row.get("campaign", {})
                metrics = row.get("metrics", {})
                segments = row.get("segments", {})
                out.append(
                    AdInsight(
                        platform=self.key,
                        account_id=account_id,
                        level=level,
                        entity_id=str(camp.get("id", "")),
                        entity_name=camp.get("name", ""),
                        date=segments.get("date", since),
                        spend=_micros(metrics.get("costMicros")) or 0.0,
                        impressions=_to_int(metrics.get("impressions")),
                        clicks=_to_int(metrics.get("clicks")),
                        conversions=_to_float(metrics.get("conversions")),
                        conversion_value=_to_float(metrics.get("conversionsValue")),
                    )
                )
        return out

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _search_stream(
        self, client: httpx.Client, token: TokenBundle, cid: str, query: str
    ) -> list[dict]:
        """POST a GAQL query to searchStream; returns the JSON array of batches."""
        url = f"{_API}/customers/{cid}/googleAds:searchStream"
        try:
            resp = client.post(url, headers=self._headers(token), json={"query": query})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Google searchStream failed: {exc}") from exc
        body = self._parse(resp)
        # searchStream returns a top-level JSON array of { results: [...] } batches.
        return body if isinstance(body, list) else []

    def _get(self, client: httpx.Client, token: TokenBundle, url: str) -> dict:
        try:
            resp = client.get(url, headers=self._headers(token))
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Google request failed: {exc}") from exc
        body = self._parse(resp)
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _parse(resp: httpx.Response):
        body = _safe_json(resp)
        if resp.status_code in (401, 403):
            raise ConnectorAuthError(f"Google Ads auth error ({resp.status_code}): {body}")
        if resp.status_code >= 400:
            raise ConnectorError(f"Google Ads API error ({resp.status_code}): {body}")
        return body


# ── Module-level utilities ─────────────────────────────────────────────────────

def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _micros(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return int(value) / 1_000_000.0
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
