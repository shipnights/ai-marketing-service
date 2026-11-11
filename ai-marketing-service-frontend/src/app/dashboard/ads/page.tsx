"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getOverview,
  getCampaigns,
  listAdAccounts,
  triggerSync,
  fmtCurrency,
  fmtNumber,
  fmtPct,
  fmtMultiple,
  PLATFORM_LABELS,
  type AdsOverview,
  type CampaignRow,
  type AdAccount,
  type AdPlatform,
} from "@/lib/ads-api";
import MetricCard from "@/components/MetricCard";
import styles from "./page.module.css";

const RANGES = [
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
];

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function AdManagerPage() {
  const [rangeDays, setRangeDays] = useState(30);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [overview, setOverview] = useState<AdsOverview | null>(null);
  const [campaigns, setCampaigns] = useState<CampaignRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const since = isoDaysAgo(rangeDays);
    const until = todayIso();

    const [accRes, ovRes, campRes] = await Promise.all([
      listAdAccounts(),
      getOverview(since, until),
      getCampaigns({ since, until, sort_by: "spend", limit: 20 }),
    ]);

    if (accRes.error) {
      setError(accRes.error);
      setLoading(false);
      return;
    }
    setAccounts(accRes.data ?? []);

    if (ovRes.error) setError(ovRes.error);
    else setOverview(ovRes.data ?? null);

    if (!campRes.error) setCampaigns(campRes.data ?? []);

    setLoading(false);
  }, [rangeDays]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSync() {
    setSyncing(true);
    await triggerSync(Math.max(rangeDays, 30));
    await load();
    setSyncing(false);
  }

  const totals = overview?.totals;
  const byPlatform = overview?.by_platform ?? {};
  const platformKeys = Object.keys(byPlatform) as AdPlatform[];

  return (
    <div>
      <div className={styles.header}>
        <h1 className={styles.title}>Ad Manager</h1>
        <div className={styles.headerActions}>
          <div className={styles.ranges}>
            {RANGES.map(({ label, days }) => (
              <button
                key={days}
                className={`${styles.rangeBtn} ${
                  rangeDays === days ? styles.rangeActive : ""
                }`}
                onClick={() => setRangeDays(days)}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            className={styles.syncBtn}
            onClick={handleSync}
            disabled={syncing}
          >
            {syncing ? "Syncing…" : "↻ Sync now"}
          </button>
        </div>
      </div>

      {loading && <p className={styles.empty}>Loading…</p>}
      {!loading && error && <p className={styles.error}>{error}</p>}

      {!loading && !error && accounts.length === 0 && (
        <div className={styles.emptyState}>
          <p className={styles.emptyTitle}>No ad accounts connected</p>
          <p className={styles.emptyText}>
            Connect an ad platform to start tracking spend, conversions, and ROAS
            across Google, Meta, TikTok, and Reddit.
          </p>
          <a href="/dashboard/integrations" className={styles.primaryBtn}>
            Connect a platform
          </a>
        </div>
      )}

      {!loading && !error && accounts.length > 0 && (
        <>
          {/* KPI grid */}
          <div className={styles.kpiGrid}>
            <MetricCard
              label="Spend"
              value={fmtCurrency(totals?.spend ?? 0)}
              accent
            />
            <MetricCard
              label="Impressions"
              value={fmtNumber(totals?.impressions ?? 0)}
            />
            <MetricCard label="Clicks" value={fmtNumber(totals?.clicks ?? 0)} />
            <MetricCard label="CTR" value={fmtPct(totals?.ctr ?? 0)} />
            <MetricCard label="Avg CPC" value={fmtCurrency(totals?.cpc ?? 0)} />
            <MetricCard
              label="Conversions"
              value={fmtNumber(totals?.conversions ?? 0)}
            />
            <MetricCard
              label="Revenue"
              value={fmtCurrency(totals?.conversion_value ?? 0)}
            />
            <MetricCard
              label="ROAS"
              value={fmtMultiple(totals?.roas ?? 0)}
              accent
            />
          </div>

          {/* By platform */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>By platform</h2>
            {platformKeys.length === 0 ? (
              <p className={styles.empty}>
                No spend recorded in this range. Try “Sync now”.
              </p>
            ) : (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.thLeft}>Platform</th>
                      <th>Spend</th>
                      <th>Impr</th>
                      <th>Clicks</th>
                      <th>CTR</th>
                      <th>Conv</th>
                      <th>ROAS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {platformKeys.map((p) => {
                      const m = byPlatform[p];
                      if (!m) return null;
                      return (
                        <tr key={p}>
                          <td className={styles.thLeft}>
                            {PLATFORM_LABELS[p] ?? p}
                          </td>
                          <td>{fmtCurrency(m.spend)}</td>
                          <td>{fmtNumber(m.impressions)}</td>
                          <td>{fmtNumber(m.clicks)}</td>
                          <td>{fmtPct(m.ctr)}</td>
                          <td>{fmtNumber(m.conversions)}</td>
                          <td>{fmtMultiple(m.roas)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Top campaigns */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>Top campaigns</h2>
            {campaigns.length === 0 ? (
              <p className={styles.empty}>No campaign data in this range.</p>
            ) : (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.thLeft}>Campaign</th>
                      <th className={styles.thLeft}>Platform</th>
                      <th>Spend</th>
                      <th>Clicks</th>
                      <th>CTR</th>
                      <th>Conv</th>
                      <th>ROAS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {campaigns.map((c) => (
                      <tr key={`${c.platform}-${c.account_id}-${c.campaign_id}`}>
                        <td className={`${styles.thLeft} ${styles.campaignName}`}>
                          {c.name}
                        </td>
                        <td className={styles.thLeft}>
                          {PLATFORM_LABELS[c.platform] ?? c.platform}
                        </td>
                        <td>{fmtCurrency(c.spend, c.currency)}</td>
                        <td>{fmtNumber(c.clicks)}</td>
                        <td>{fmtPct(c.ctr)}</td>
                        <td>{fmtNumber(c.conversions)}</td>
                        <td>{fmtMultiple(c.roas)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
