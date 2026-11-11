"""Normalized ad-platform data model.

Every connector (Google, Meta, TikTok, Reddit) returns these shapes regardless of
the platform's native API, so the rest of the app — storage, aggregation, the
dashboard — speaks one language.

Core (summable) metrics are stored raw. Derived ratios (CTR, CPC, CPM, CPA, ROAS,
CVR) are always recomputed from the raw totals so they aggregate correctly across
platforms and date ranges (you cannot average an average).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ── Platform registry ─────────────────────────────────────────────────────────
# Connector / provider keys. Kept distinct from the social-publishing platform
# keys (instagram/facebook/tiktok) so paid-ads and organic stay unambiguous.
AD_PLATFORMS: dict[str, str] = {
    "google_ads": "Google Ads",
    "meta_ads": "Meta Ads",
    "tiktok_ads": "TikTok Ads",
    "reddit_ads": "Reddit Ads",
}


def is_ad_platform(platform: str) -> bool:
    return platform in AD_PLATFORMS


def platform_label(platform: str) -> str:
    return AD_PLATFORMS.get(platform, platform)


# ── Account / campaign / insight shapes ───────────────────────────────────────

@dataclass
class AdAccountInfo:
    """An advertising account discovered under a connection."""

    platform: str
    account_id: str          # platform-native id (e.g. Google customer id, Meta act_<id>)
    name: str
    currency: str = "USD"
    status: str = "active"   # active | disabled | unknown
    timezone: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Campaign:
    platform: str
    account_id: str
    campaign_id: str
    name: str
    status: str = "unknown"          # active | paused | removed | unknown
    objective: str | None = None
    daily_budget: float | None = None
    lifetime_budget: float | None = None
    start_date: str | None = None
    end_date: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# Raw, summable metric fields a connector must populate (others optional).
CORE_METRICS = (
    "spend",
    "impressions",
    "clicks",
    "conversions",
    "conversion_value",
    "reach",
    "video_views",
)


@dataclass
class AdInsight:
    """Normalized metrics for one entity (account/campaign) on one date.

    Connectors fill the raw CORE_METRICS; call `with_derived()` (or rely on
    `metrics_dict`) to attach CTR/CPC/CPM/CPA/ROAS/CVR.
    """

    platform: str
    account_id: str
    level: str = "account"           # account | campaign | adset | ad
    entity_id: str = ""
    entity_name: str = ""
    date: str = ""                   # YYYY-MM-DD (UTC); "" means range/rollup
    currency: str = "USD"

    spend: float = 0.0
    impressions: int = 0
    clicks: int = 0
    conversions: float = 0.0
    conversion_value: float = 0.0    # revenue attributed by the ad platform
    reach: int = 0
    video_views: int = 0

    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(compute_derived(d))
        return d


# ── Derived metric math ───────────────────────────────────────────────────────

def _safe_div(n: float, d: float) -> float:
    return round(n / d, 4) if d else 0.0


def compute_derived(m: dict) -> dict:
    """Compute ratio metrics from raw totals. Works on a single row or an
    aggregated sum — that's the whole point of recomputing rather than averaging."""
    spend = float(m.get("spend", 0) or 0)
    impressions = float(m.get("impressions", 0) or 0)
    clicks = float(m.get("clicks", 0) or 0)
    conversions = float(m.get("conversions", 0) or 0)
    revenue = float(m.get("conversion_value", 0) or 0)
    return {
        "ctr": _safe_div(clicks, impressions),          # click-through rate
        "cpc": _safe_div(spend, clicks),                # cost per click
        "cpm": _safe_div(spend * 1000, impressions),    # cost per 1k impressions
        "cpa": _safe_div(spend, conversions),           # cost per acquisition
        "cvr": _safe_div(conversions, clicks),          # conversion rate
        "roas": _safe_div(revenue, spend),              # return on ad spend
    }


def empty_totals() -> dict:
    return {k: 0.0 for k in CORE_METRICS}


def sum_insights(rows: list[dict]) -> dict:
    """Sum CORE_METRICS across rows and attach derived metrics."""
    totals = empty_totals()
    for r in rows:
        for k in CORE_METRICS:
            totals[k] += float(r.get(k, 0) or 0)
    totals.update(compute_derived(totals))
    return totals
