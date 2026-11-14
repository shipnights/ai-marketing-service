"""Test doubles shared by the ads / integrations / sync tests.

FakeConnector implements the AdConnector interface with canned, configurable
behaviour so route and sync tests never touch a real ad platform. Install it
with `install_fake_connector(monkeypatch, ...)`, which swaps it into the
connector registry for the duration of one test.
"""

from app.services.ads.base import AdConnector, TokenBundle
from app.services.ads.schema import AdAccountInfo, AdInsight, Campaign


class FakeConnector(AdConnector):
    label = "Fake Ads"

    def __init__(self, key="meta_ads", *, configured=True, accounts=None, campaigns=None,
                 insights=None, token=None, errors=None, refreshed=None):
        self.key = key
        self._configured = configured
        self.accounts = accounts if accounts is not None else [
            AdAccountInfo(platform=key, account_id="act_1", name="Main", currency="USD")
        ]
        self.campaigns = campaigns if campaigns is not None else [
            Campaign(platform=key, account_id="act_1", campaign_id="c1", name="Spring", status="active")
        ]
        self.insights = insights if insights is not None else []
        self.token = token or TokenBundle(access_token="fake-access", refresh_token="fake-refresh",
                                          expires_at=None, scope="ads_read")
        self.refreshed = refreshed or TokenBundle(access_token="refreshed-access", refresh_token=None,
                                                  expires_at=None)
        # method name -> exception instance to raise
        self.errors = errors or {}
        self.calls: list[tuple] = []

    def _maybe_raise(self, name):
        if name in self.errors:
            raise self.errors[name]

    @property
    def is_configured(self) -> bool:
        return self._configured

    def authorize_url(self, state, redirect_uri=None):
        self.calls.append(("authorize_url", state))
        return f"https://fake.example/oauth?state={state}"

    def exchange_code(self, code, redirect_uri=None):
        self.calls.append(("exchange_code", code))
        self._maybe_raise("exchange_code")
        return self.token

    def refresh(self, refresh_token):
        self.calls.append(("refresh", refresh_token))
        self._maybe_raise("refresh")
        return self.refreshed

    def list_ad_accounts(self, token):
        self.calls.append(("list_ad_accounts", token.access_token))
        self._maybe_raise("list_ad_accounts")
        return list(self.accounts)

    def fetch_campaigns(self, token, account_id):
        self.calls.append(("fetch_campaigns", account_id))
        self._maybe_raise("fetch_campaigns")
        return list(self.campaigns)

    def fetch_insights(self, token, account_id, since, until, level="campaign"):
        self.calls.append(("fetch_insights", account_id, since, until, token.access_token))
        self._maybe_raise("fetch_insights")
        return [i for i in self.insights if i.account_id == account_id]


def install_fake_connector(monkeypatch, connector: FakeConnector) -> FakeConnector:
    """Register `connector` in the ads registry under its key for one test."""
    from app.services.ads import registry

    monkeypatch.setitem(registry._INSTANCES, connector.key, connector)
    return connector


def insight(platform="meta_ads", account_id="act_1", entity_id="c1", date="2024-01-10",
            level="campaign", **metrics) -> AdInsight:
    """Shorthand AdInsight builder."""
    return AdInsight(platform=platform, account_id=account_id, level=level, entity_id=entity_id,
                     entity_name=metrics.pop("entity_name", f"Campaign {entity_id}"),
                     date=date, **metrics)
