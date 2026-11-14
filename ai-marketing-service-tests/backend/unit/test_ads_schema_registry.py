"""Unit tests for the platform-agnostic ads layer: schema math, TokenBundle,
and the connector registry."""

import time

import pytest

from app.services.ads import registry, schema
from app.services.ads.base import AdConnector, ConnectorConfigError, TokenBundle


# -- derived metrics -----------------------------------------------------------------

def test_compute_derived_all_ratios():
    d = schema.compute_derived({"spend": 100, "impressions": 10_000, "clicks": 200,
                                "conversions": 10, "conversion_value": 500})
    assert d == {"ctr": 0.02, "cpc": 0.5, "cpm": 10.0, "cpa": 10.0, "cvr": 0.05, "roas": 5.0}


def test_compute_derived_zero_denominators_are_zero_not_errors():
    assert schema.compute_derived({}) == {"ctr": 0.0, "cpc": 0.0, "cpm": 0.0, "cpa": 0.0, "cvr": 0.0, "roas": 0.0}
    assert schema.compute_derived({"spend": None, "clicks": ""})["cpc"] == 0.0


def test_compute_derived_rounds_to_4_places():
    assert schema.compute_derived({"clicks": 1, "impressions": 3})["ctr"] == 0.3333


def test_sum_insights_recomputes_ratios_instead_of_averaging():
    """Averaging CTRs (0.5 and 0.01 -> 0.255) is wrong; the true blended CTR
    is total clicks / total impressions = 51/1100."""
    rows = [{"clicks": 50, "impressions": 100}, {"clicks": 1, "impressions": 1000}]
    totals = schema.sum_insights(rows)
    assert totals["clicks"] == 51.0 and totals["impressions"] == 1100.0
    assert totals["ctr"] == round(51 / 1100, 4)


def test_sum_insights_empty_and_missing_fields():
    assert schema.sum_insights([])["spend"] == 0.0
    assert schema.sum_insights([{"spend": None}, {"spend": "2.5"}])["spend"] == 2.5


def test_ad_insight_to_dict_includes_raw_and_derived():
    d = schema.AdInsight(platform="meta_ads", account_id="a", spend=10.0, clicks=5).to_dict()
    assert d["spend"] == 10.0 and d["cpc"] == 2.0
    assert set(schema.CORE_METRICS) <= set(d)


def test_platform_helpers():
    assert schema.is_ad_platform("google_ads") and not schema.is_ad_platform("instagram")
    assert schema.platform_label("reddit_ads") == "Reddit Ads"
    assert schema.platform_label("unknown") == "unknown"


# -- TokenBundle ----------------------------------------------------------------------

def test_token_without_expiry_never_expires():
    assert TokenBundle(access_token="x").is_expired() is False


def test_token_expiry_uses_skew():
    now = int(time.time())
    assert TokenBundle(access_token="x", expires_at=now - 1).is_expired() is True
    # Inside the default 120 s skew window it already counts as expired...
    assert TokenBundle(access_token="x", expires_at=now + 60).is_expired() is True
    # ...but not when well in the future.
    assert TokenBundle(access_token="x", expires_at=now + 3600).is_expired() is False
    assert TokenBundle(access_token="x", expires_at=now + 60).is_expired(skew=0) is False


# -- registry --------------------------------------------------------------------------

def test_registry_has_exactly_the_four_platforms():
    assert {c.key for c in registry.all_connectors()} == set(schema.AD_PLATFORMS)


def test_registry_keys_and_labels_match_schema():
    for c in registry.all_connectors():
        assert isinstance(c, AdConnector)
        assert c.label == schema.AD_PLATFORMS[c.key]


def test_get_connector_unknown_raises_config_error():
    with pytest.raises(ConnectorConfigError, match="Unknown ad platform"):
        registry.get_connector("snapchat_ads")


def test_connector_status_unconfigured_in_tests():
    status = registry.connector_status()
    assert {s["platform"] for s in status} == set(schema.AD_PLATFORMS)
    for s in status:
        assert s["configured"] is False           # no platform credentials in test env
        assert s["supports_management"] is False  # read-only phase
        assert set(s) == {"platform", "label", "configured", "supports_management"}


@pytest.mark.parametrize("platform", list(schema.AD_PLATFORMS))
def test_unconfigured_connectors_refuse_oauth(platform):
    c = registry.get_connector(platform)
    with pytest.raises(ConnectorConfigError):
        c.authorize_url("state")
    with pytest.raises(ConnectorConfigError):
        c.exchange_code("code")
