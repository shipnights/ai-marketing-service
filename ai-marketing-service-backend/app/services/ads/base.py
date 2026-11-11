"""Ad connector abstraction.

Each platform implements `AdConnector`. The OAuth + insights routes and the sync
worker depend only on this interface, never on a specific platform's quirks.

OAuth shape across platforms (all four are OAuth2 authorization-code):
    authorize_url()  → user consents on the platform
    exchange_code()  → code → access/refresh tokens
    refresh()        → refresh_token → fresh access token
    list_ad_accounts / fetch_campaigns / fetch_insights → data
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.services.ads.schema import AdAccountInfo, AdInsight, Campaign


# ── Errors ────────────────────────────────────────────────────────────────────

class ConnectorError(Exception):
    """Generic connector failure (network, unexpected API response)."""


class ConnectorConfigError(ConnectorError):
    """The connector's app credentials are not configured on this server."""


class ConnectorAuthError(ConnectorError):
    """Token is invalid/expired and refresh failed — user must reconnect."""


# ── Token bundle ──────────────────────────────────────────────────────────────

@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None        # unix seconds; None = unknown/long-lived
    scope: str | None = None
    token_type: str = "Bearer"
    raw: dict = field(default_factory=dict)

    def is_expired(self, skew: int = 120) -> bool:
        if self.expires_at is None:
            return False
        return time.time() >= (self.expires_at - skew)


# ── Connector interface ───────────────────────────────────────────────────────

class AdConnector(ABC):
    #: provider key, e.g. "google_ads"
    key: str = ""
    #: human label, e.g. "Google Ads"
    label: str = ""

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True when this server has the app credentials needed to run OAuth."""

    @abstractmethod
    def authorize_url(self, state: str, redirect_uri: str | None = None) -> str:
        """Build the platform consent URL the user is redirected to."""

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str | None = None) -> TokenBundle:
        """Exchange an authorization code for tokens."""

    @abstractmethod
    def refresh(self, refresh_token: str) -> TokenBundle:
        """Exchange a refresh token for a fresh access token."""

    @abstractmethod
    def list_ad_accounts(self, token: TokenBundle) -> list[AdAccountInfo]:
        """List the advertising accounts the connected user can access."""

    @abstractmethod
    def fetch_campaigns(self, token: TokenBundle, account_id: str) -> list[Campaign]:
        """List campaigns under an ad account."""

    @abstractmethod
    def fetch_insights(
        self,
        token: TokenBundle,
        account_id: str,
        since: str,
        until: str,
        level: str = "campaign",
    ) -> list[AdInsight]:
        """Fetch per-day insights between since/until (YYYY-MM-DD inclusive)."""

    # Optional capability flags consumed by the UI.
    supports_management: bool = False
