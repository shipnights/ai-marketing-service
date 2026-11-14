"""Unit tests for app/db/ad_stats.py - insight storage & dashboard aggregation."""

import pytest

from app.db import ad_stats
from app.db.tables import ANALYTICS_TABLE
from backend.fixtures.fakes import insight

TEAM = "t1"


def _store(*rows):
    assert ad_stats.store_insights(TEAM, list(rows)) is True


# -- store / query -----------------------------------------------------------------

def test_store_and_query_roundtrip_strips_keys_and_adds_derived(dynamodb):
    _store(insight(spend=50.0, impressions=1000, clicks=20, conversions=2, conversion_value=200.0))
    rows = ad_stats.query_insights(TEAM, "2024-01-01", "2024-01-31")
    assert len(rows) == 1
    r = rows[0]
    assert "pk" not in r and "sk" not in r
    assert r["spend"] == 50 and r["clicks"] == 20
    # Derived ratios are recomputed on read.
    assert r["ctr"] == 0.02 and r["cpc"] == 2.5 and r["cpm"] == 50.0
    assert r["cpa"] == 25.0 and r["roas"] == 4.0 and r["cvr"] == 0.1


def test_store_is_idempotent_per_day_entity(dynamodb):
    """Re-syncing a day overwrites the same row instead of double-counting."""
    _store(insight(spend=10.0))
    _store(insight(spend=12.5))                    # same date/platform/account/entity
    rows = ad_stats.query_insights(TEAM, "2024-01-01", "2024-01-31")
    assert len(rows) == 1
    assert float(rows[0]["spend"]) == 12.5


def test_store_empty_batch_is_ok(dynamodb):
    assert ad_stats.store_insights(TEAM, []) is True


def test_missing_entity_id_uses_placeholder_sk(dynamodb):
    _store(insight(entity_id="", level="account", spend=1.0))
    item = dynamodb.Table(ANALYTICS_TABLE).scan()["Items"][0]
    assert item["sk"].endswith("#account#_")


def test_query_window_is_inclusive_on_both_ends(dynamodb):
    _store(insight(date="2023-12-31", entity_id="before"),
           insight(date="2024-01-01", entity_id="first"),
           insight(date="2024-01-31", entity_id="last"),
           insight(date="2024-02-01", entity_id="after"))
    ids = {r["entity_id"] for r in ad_stats.query_insights(TEAM, "2024-01-01", "2024-01-31")}
    assert ids == {"first", "last"}


def test_query_filters_platform_account_level(dynamodb):
    _store(insight(platform="meta_ads", account_id="a1", entity_id="m"),
           insight(platform="google_ads", account_id="g1", entity_id="g"),
           insight(platform="google_ads", account_id="g2", entity_id="g2c"),
           insight(platform="google_ads", account_id="g1", entity_id="acct", level="account"))
    q = lambda **kw: {r["entity_id"] for r in ad_stats.query_insights(TEAM, "2024-01-01", "2024-01-31", **kw)}
    assert q(platform="google_ads") == {"g", "g2c", "acct"}
    assert q(platform="google_ads", account_id="g1") == {"g", "acct"}
    assert q(level="account") == {"acct"}


def test_teams_are_isolated(dynamodb):
    _store(insight(entity_id="mine"))
    ad_stats.store_insights("other-team", [insight(entity_id="theirs")])
    assert {r["entity_id"] for r in ad_stats.query_insights(TEAM, "2024-01-01", "2024-01-31")} == {"mine"}


# -- summary -------------------------------------------------------------------------

def test_summary_totals_platforms_and_timeseries(dynamodb):
    _store(insight(platform="meta_ads", date="2024-01-01", entity_id="m1", spend=10.0, clicks=5, impressions=100),
           insight(platform="meta_ads", date="2024-01-02", entity_id="m1", spend=20.0, clicks=5, impressions=100),
           insight(platform="google_ads", date="2024-01-01", entity_id="g1", spend=30.0, clicks=10, impressions=800))
    s = ad_stats.summary(TEAM, "2024-01-01", "2024-01-31")
    assert (s["since"], s["until"]) == ("2024-01-01", "2024-01-31")
    assert s["totals"]["spend"] == 60.0
    assert s["totals"]["clicks"] == 20.0
    # CTR is recomputed from summed totals (20/1000), NOT averaged per row.
    assert s["totals"]["ctr"] == 0.02
    assert list(s["by_platform"]) == ["google_ads", "meta_ads"]          # sorted
    assert s["by_platform"]["meta_ads"]["spend"] == 30.0
    assert [(p["date"], p["spend"]) for p in s["timeseries"]] == [("2024-01-01", 40.0), ("2024-01-02", 20.0)]


def test_summary_uses_single_level_to_avoid_double_counting(dynamodb):
    _store(insight(level="campaign", entity_id="c1", spend=10.0),
           insight(level="account", entity_id="", spend=10.0))        # same money, other level
    assert ad_stats.summary(TEAM, "2024-01-01", "2024-01-31")["totals"]["spend"] == 10.0


def test_summary_empty_window(dynamodb):
    s = ad_stats.summary(TEAM, "2024-01-01", "2024-01-31")
    assert s["totals"]["spend"] == 0.0
    assert s["by_platform"] == {} and s["timeseries"] == []


@pytest.mark.xfail(strict=True, reason=(
    "BUG: query_insights -> query_by_pk_sk_between(limit=1000) caps the result "
    "at 1000 rows, so dashboard totals are SILENTLY truncated for busy teams "
    "(e.g. 4 platforms x 10 campaigns x 30 days = 1200 rows)."))
def test_summary_counts_more_than_1000_rows(dynamodb):
    rows = [insight(entity_id=f"c{i:04d}", date="2024-01-15", spend=1.0) for i in range(1001)]
    _store(*rows)
    assert ad_stats.summary(TEAM, "2024-01-01", "2024-01-31")["totals"]["spend"] == 1001.0


# -- top_campaigns ----------------------------------------------------------------------

def test_top_campaigns_aggregates_across_days_and_ranks(dynamodb):
    _store(insight(entity_id="a", date="2024-01-01", spend=5.0, entity_name="Alpha"),
           insight(entity_id="a", date="2024-01-02", spend=5.0, entity_name="Alpha"),
           insight(entity_id="b", date="2024-01-01", spend=8.0, entity_name="Beta"),
           insight(entity_id="c", date="2024-01-01", spend=1.0, entity_name="Gamma"))
    top = ad_stats.top_campaigns(TEAM, "2024-01-01", "2024-01-31")
    assert [(c["campaign_id"], c["spend"]) for c in top] == [("a", 10.0), ("b", 8.0), ("c", 1.0)]
    assert top[0]["name"] == "Alpha"
    assert top[0]["platform"] == "meta_ads" and top[0]["currency"] == "USD"


def test_top_campaigns_sort_by_derived_metric_and_limit(dynamodb):
    _store(insight(entity_id="cheap", spend=10.0, conversion_value=100.0),    # ROAS 10
           insight(entity_id="pricey", spend=100.0, conversion_value=150.0))  # ROAS 1.5
    top = ad_stats.top_campaigns(TEAM, "2024-01-01", "2024-01-31", sort_by="roas", limit=1)
    assert [c["campaign_id"] for c in top] == ["cheap"]


def test_top_campaigns_same_id_on_different_platforms_not_merged(dynamodb):
    _store(insight(platform="meta_ads", entity_id="42", spend=1.0),
           insight(platform="google_ads", entity_id="42", spend=2.0))
    assert len(ad_stats.top_campaigns(TEAM, "2024-01-01", "2024-01-31")) == 2
