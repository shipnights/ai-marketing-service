"""Ad insight storage & aggregation (ANALYTICS_TABLE).

Item layout — one partition per team holds every ad insight row so the dashboard
can range-scan all platforms at once:
    pk = ADSTATS#{team_id}
    sk = {date}#{platform}#{account_id}#{level}#{entity_id}

`date`-first sort keys make a BETWEEN(since, until) query return the whole window
in date order. Re-syncing a day overwrites the same sk, so sync is idempotent.

Only raw (summable) metrics are stored; ratios (CTR/CPC/ROAS/…) are recomputed on
read via schema.compute_derived so they aggregate correctly.
"""

from typing import Any

from app.db.main import put_items_batch, query_by_pk_sk_between
from app.db.tables import ANALYTICS_TABLE
from app.services.ads.schema import CORE_METRICS, AdInsight, compute_derived, sum_insights

_SK_HI_SUFFIX = "￿"  # sorts after any real sk with the same date


def _pk(team_id: str) -> str:
    return f"ADSTATS#{team_id}"


def _sk(date: str, platform: str, account_id: str, level: str, entity_id: str) -> str:
    return f"{date}#{platform}#{account_id}#{level}#{entity_id}"


def store_insights(team_id: str, insights: list[AdInsight]) -> bool:
    """Persist a batch of normalized insights for a team (idempotent per row)."""
    items: list[dict[str, Any]] = []
    for ins in insights:
        sk = _sk(ins.date, ins.platform, ins.account_id, ins.level, ins.entity_id or "_")
        items.append({
            "pk": _pk(team_id),
            "sk": sk,
            "team_id": team_id,
            "platform": ins.platform,
            "account_id": ins.account_id,
            "level": ins.level,
            "entity_id": ins.entity_id,
            "entity_name": ins.entity_name,
            "date": ins.date,
            "currency": ins.currency,
            **{k: getattr(ins, k) for k in CORE_METRICS},
        })
    return put_items_batch(items, table_name=ANALYTICS_TABLE)


def query_insights(
    team_id: str,
    since: str,
    until: str,
    *,
    platform: str | None = None,
    account_id: str | None = None,
    level: str | None = None,
) -> list[dict[str, Any]]:
    """Return raw insight rows in [since, until], optionally filtered. Derived
    metrics are attached to each row."""
    rows = query_by_pk_sk_between(
        _pk(team_id), since, f"{until}{_SK_HI_SUFFIX}", table_name=ANALYTICS_TABLE
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        if platform and r.get("platform") != platform:
            continue
        if account_id and r.get("account_id") != account_id:
            continue
        if level and r.get("level") != level:
            continue
        r = {k: v for k, v in r.items() if k not in ("pk", "sk")}
        r.update(compute_derived(r))
        out.append(r)
    return out


# ── Aggregations for the dashboard ────────────────────────────────────────────

def summary(team_id: str, since: str, until: str, *, level: str = "campaign") -> dict[str, Any]:
    """Cross-platform rollup for a date window: grand totals, per-platform totals,
    and a daily time series. Uses one level (default campaign) to avoid double-counting."""
    rows = query_insights(team_id, since, until, level=level)

    by_platform: dict[str, list[dict]] = {}
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_platform.setdefault(r["platform"], []).append(r)
        by_date.setdefault(r["date"], []).append(r)

    return {
        "since": since,
        "until": until,
        "totals": sum_insights(rows),
        "by_platform": {p: sum_insights(rs) for p, rs in sorted(by_platform.items())},
        "timeseries": [
            {"date": d, **sum_insights(by_date[d])} for d in sorted(by_date)
        ],
    }


def top_campaigns(
    team_id: str, since: str, until: str, *, sort_by: str = "spend", limit: int = 10
) -> list[dict[str, Any]]:
    """Aggregate campaign-level rows across the window and return the top N."""
    rows = query_insights(team_id, since, until, level="campaign")
    grouped: dict[tuple, list[dict]] = {}
    meta: dict[tuple, dict] = {}
    for r in rows:
        key = (r["platform"], r["account_id"], r["entity_id"])
        grouped.setdefault(key, []).append(r)
        meta[key] = {
            "platform": r["platform"],
            "account_id": r["account_id"],
            "campaign_id": r["entity_id"],
            "name": r.get("entity_name", ""),
            "currency": r.get("currency", "USD"),
        }
    campaigns = [{**meta[k], **sum_insights(v)} for k, v in grouped.items()]
    campaigns.sort(key=lambda c: c.get(sort_by, 0), reverse=True)
    return campaigns[:limit]
