"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getPlans,
  getSubscription,
  getCredits,
  startCheckout,
  startCreditCheckout,
  openPortal,
  type BillingCatalog,
  type Subscription,
  type CreditsInfo,
  type Plan,
  type Pack,
  type PlanKey,
  type BillingInterval,
} from "@/lib/billing-api";
import styles from "./page.module.css";

// ── Local formatting helpers ───────────────────────────────────────────────

function fmtUsd(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function fmtNumber(value: number): string {
  return value.toLocaleString("en-US");
}

function fmtLimit(value: number): string {
  // backend uses -1 / very large numbers to signal "unlimited"
  if (value < 0 || value >= 1_000_000) return "Unlimited";
  return fmtNumber(value);
}

function fmtDate(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function fmtLedgerDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

const LIMIT_ROWS: { key: keyof Plan["limits"]; label: string }[] = [
  { key: "projects", label: "Projects" },
  { key: "social_accounts", label: "Social accounts" },
  { key: "ad_accounts", label: "Ad accounts" },
  { key: "team_members", label: "Team members" },
  { key: "storage_gb", label: "Storage (GB)" },
];

export default function BillingPage() {
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [credits, setCredits] = useState<CreditsInfo | null>(null);
  const [interval, setInterval] = useState<BillingInterval>("monthly");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [plansRes, subRes, credRes] = await Promise.all([
      getPlans(),
      getSubscription(),
      getCredits(),
    ]);
    if (plansRes.error) {
      setError(plansRes.error);
      setLoading(false);
      return;
    }
    setCatalog(plansRes.data ?? null);
    if (!subRes.error) setSubscription(subRes.data ?? null);
    if (!credRes.error) setCredits(credRes.data ?? null);
    setError(null);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const stripeConfigured = catalog?.stripe_configured ?? false;
  const currentPlanKey = subscription?.plan;

  async function handleUpgrade(planKey: PlanKey) {
    setBusyKey(`plan-${planKey}`);
    setActionError(null);
    const { data, error } = await startCheckout(planKey, interval);
    if (error || !data?.url) {
      setActionError(error ?? "Could not start checkout.");
      setBusyKey(null);
      return;
    }
    window.location.assign(data.url);
  }

  async function handleBuyPack(packKey: string) {
    setBusyKey(`pack-${packKey}`);
    setActionError(null);
    const { data, error } = await startCreditCheckout(packKey);
    if (error || !data?.url) {
      setActionError(error ?? "Could not start checkout.");
      setBusyKey(null);
      return;
    }
    window.location.assign(data.url);
  }

  async function handlePortal() {
    setBusyKey("portal");
    setActionError(null);
    const { data, error } = await openPortal();
    if (error || !data?.url) {
      setActionError(error ?? "Could not open billing portal.");
      setBusyKey(null);
      return;
    }
    window.location.assign(data.url);
  }

  return (
    <div>
      <div className={styles.header}>
        <h1 className={styles.title}>Billing</h1>
      </div>

      {loading && <p className={styles.empty}>Loading…</p>}
      {!loading && error && <p className={styles.error}>{error}</p>}

      {!loading && !error && (
        <>
          {actionError && <p className={styles.error}>{actionError}</p>}

          {!stripeConfigured && (
            <div className={styles.notice}>Billing not configured.</div>
          )}

          {/* Current plan + credits header */}
          {subscription && (
            <div className={styles.summary}>
              <div className={styles.summaryMain}>
                <div className={styles.summaryRow}>
                  <span className={styles.summaryPlan}>
                    {subscription.plan
                      ? subscription.plan.charAt(0).toUpperCase() +
                        subscription.plan.slice(1)
                      : "No plan"}
                  </span>
                  <span
                    className={`${styles.statusBadge} ${
                      subscription.status === "active"
                        ? styles.statusActive
                        : styles.statusInactive
                    }`}
                  >
                    {subscription.status || "inactive"}
                  </span>
                </div>
                <div className={styles.summaryMeta}>
                  {subscription.interval && (
                    <span>Billed {subscription.interval}</span>
                  )}
                  {subscription.current_period_end != null && (
                    <span>
                      {subscription.cancel_at_period_end ? "Ends" : "Renews"}{" "}
                      {fmtDate(subscription.current_period_end)}
                    </span>
                  )}
                </div>
                {subscription.cancel_at_period_end && (
                  <p className={styles.cancelNote}>
                    Your subscription is set to cancel at the end of the current
                    period.
                  </p>
                )}
              </div>

              <div className={styles.summaryCredits}>
                <span className={styles.creditsLabel}>Credits balance</span>
                <span className={styles.creditsValue}>
                  {fmtNumber(subscription.credits_balance)}
                </span>
                {subscription.has_customer && (
                  <button
                    className={styles.secondaryBtn}
                    onClick={handlePortal}
                    disabled={busyKey === "portal"}
                  >
                    {busyKey === "portal" ? "Opening…" : "Manage billing"}
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Plans */}
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>Plans</h2>
              <div className={styles.intervalToggle}>
                <button
                  className={`${styles.intervalBtn} ${
                    interval === "monthly" ? styles.intervalActive : ""
                  }`}
                  onClick={() => setInterval("monthly")}
                >
                  Monthly
                </button>
                <button
                  className={`${styles.intervalBtn} ${
                    interval === "annual" ? styles.intervalActive : ""
                  }`}
                  onClick={() => setInterval("annual")}
                >
                  Annual
                </button>
              </div>
            </div>

            <div className={styles.planGrid}>
              {(catalog?.plans ?? []).map((plan: Plan) => {
                const price =
                  interval === "annual"
                    ? plan.price_annual_usd
                    : plan.price_monthly_usd;
                const isCurrent = currentPlanKey === plan.key;
                const disabled =
                  !plan.configured || !stripeConfigured || isCurrent;
                return (
                  <div
                    key={plan.key}
                    className={`${styles.planCard} ${
                      isCurrent ? styles.planCurrent : ""
                    }`}
                  >
                    <h3 className={styles.planName}>{plan.name}</h3>
                    <div className={styles.planPrice}>
                      <span className={styles.priceAmount}>
                        {fmtUsd(price)}
                      </span>
                      <span className={styles.pricePer}>
                        /{interval === "annual" ? "yr" : "mo"}
                      </span>
                    </div>
                    <p className={styles.planCredits}>
                      {fmtNumber(plan.monthly_credits)} credits / month
                    </p>

                    <ul className={styles.limitList}>
                      {LIMIT_ROWS.map(({ key, label }) => (
                        <li key={key} className={styles.limitItem}>
                          <span>{label}</span>
                          <span className={styles.limitValue}>
                            {fmtLimit(plan.limits[key])}
                          </span>
                        </li>
                      ))}
                    </ul>

                    {plan.features.length > 0 && (
                      <ul className={styles.featureList}>
                        {plan.features.map((f, i) => (
                          <li key={i} className={styles.featureItem}>
                            <span className={styles.check}>✓</span>
                            {f}
                          </li>
                        ))}
                      </ul>
                    )}

                    <button
                      className={
                        isCurrent ? styles.currentBtn : styles.primaryBtn
                      }
                      onClick={() => handleUpgrade(plan.key)}
                      disabled={disabled}
                    >
                      {isCurrent
                        ? "Current plan"
                        : busyKey === `plan-${plan.key}`
                          ? "Redirecting…"
                          : !plan.configured || !stripeConfigured
                            ? "Billing not configured"
                            : "Upgrade"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Credit packs */}
          {(catalog?.credit_packs ?? []).length > 0 && (
            <div className={styles.section}>
              <h2 className={styles.sectionTitle}>Credit packs</h2>
              <div className={styles.packGrid}>
                {(catalog?.credit_packs ?? []).map((pack: Pack) => {
                  const disabled = !pack.configured || !stripeConfigured;
                  return (
                    <div key={pack.key} className={styles.packCard}>
                      <div>
                        <h3 className={styles.packName}>{pack.name}</h3>
                        <p className={styles.packCredits}>
                          {fmtNumber(pack.credits)} credits
                        </p>
                      </div>
                      <div className={styles.packBuy}>
                        <span className={styles.packPrice}>
                          {fmtUsd(pack.price_usd)}
                        </span>
                        <button
                          className={styles.secondaryBtn}
                          onClick={() => handleBuyPack(pack.key)}
                          disabled={disabled || busyKey === `pack-${pack.key}`}
                        >
                          {busyKey === `pack-${pack.key}` ? "…" : "Buy"}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Credit ledger */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>Credit history</h2>
            {!credits || credits.ledger.length === 0 ? (
              <p className={styles.empty}>No credit activity yet.</p>
            ) : (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.thLeft}>Date</th>
                      <th className={styles.thLeft}>Reason</th>
                      <th>Change</th>
                      <th>Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {credits.ledger.map((entry, i) => (
                      <tr key={i}>
                        <td className={styles.thLeft}>
                          {fmtLedgerDate(entry.created_at)}
                        </td>
                        <td className={styles.thLeft}>{entry.reason}</td>
                        <td
                          className={
                            entry.delta >= 0 ? styles.deltaPos : styles.deltaNeg
                          }
                        >
                          {entry.delta >= 0 ? "+" : ""}
                          {fmtNumber(entry.delta)}
                        </td>
                        <td>{fmtNumber(entry.balance_after)}</td>
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
