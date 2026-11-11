"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  listIntegrations,
  getConnectUrl,
  listPlatformAccounts,
  refreshPlatformAccounts,
  setAccountEnabled,
  disconnectPlatform,
  type Integration,
  type AdAccount,
} from "@/lib/integrations-api";
import styles from "./page.module.css";

// ============================================================================
// Per-platform card
// ============================================================================

function IntegrationCard({
  integration,
  onChanged,
}: {
  integration: Integration;
  onChanged: () => void;
}) {
  const { platform } = integration;
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadAccounts = useCallback(async () => {
    if (!integration.connected) return;
    setAccountsLoading(true);
    const { data, error } = await listPlatformAccounts(platform);
    if (!error) setAccounts(data ?? []);
    setAccountsLoading(false);
  }, [platform, integration.connected]);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  async function handleConnect() {
    setBusy(true);
    setActionError(null);
    const { data, error } = await getConnectUrl(platform);
    if (error || !data?.authorize_url) {
      setActionError(error ?? "Could not start authorization.");
      setBusy(false);
      return;
    }
    window.location.assign(data.authorize_url);
  }

  async function handleRefresh() {
    setBusy(true);
    setActionError(null);
    const { error } = await refreshPlatformAccounts(platform);
    if (error) setActionError(error);
    await loadAccounts();
    setBusy(false);
  }

  async function handleToggle(accountId: string, enabled: boolean) {
    setActionError(null);
    // optimistic update
    setAccounts((prev) =>
      prev.map((a) => (a.account_id === accountId ? { ...a, enabled } : a))
    );
    const { error } = await setAccountEnabled(platform, accountId, enabled);
    if (error) {
      setActionError(error);
      // revert
      setAccounts((prev) =>
        prev.map((a) =>
          a.account_id === accountId ? { ...a, enabled: !enabled } : a
        )
      );
    } else {
      onChanged();
    }
  }

  async function handleDisconnect() {
    if (
      !window.confirm(
        `Disconnect ${integration.label}? This removes its ad accounts and stored credentials.`
      )
    ) {
      return;
    }
    setBusy(true);
    setActionError(null);
    const { error } = await disconnectPlatform(platform);
    setBusy(false);
    if (error) {
      setActionError(error);
      return;
    }
    setAccounts([]);
    onChanged();
  }

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h3 className={styles.cardName}>{integration.label}</h3>
          <span
            className={`${styles.statusPill} ${
              integration.connected ? styles.statusOn : styles.statusOff
            }`}
          >
            {integration.connected ? "Connected" : "Not connected"}
          </span>
        </div>
        {integration.connected && (
          <span className={styles.accountCount}>
            {integration.enabled_account_count}/{integration.account_count}{" "}
            accounts enabled
          </span>
        )}
      </div>

      {!integration.configured && (
        <p className={styles.muted}>Not available — server not configured.</p>
      )}

      {integration.configured &&
        integration.connected &&
        integration.connection_status && (
          <p className={styles.connStatus}>
            Status: {integration.connection_status}
          </p>
        )}

      {integration.last_error && (
        <p className={styles.cardError}>{integration.last_error}</p>
      )}

      {actionError && <p className={styles.cardError}>{actionError}</p>}

      {/* Connected → account list */}
      {integration.configured && integration.connected && (
        <div className={styles.accounts}>
          {accountsLoading && (
            <p className={styles.muted}>Loading accounts…</p>
          )}
          {!accountsLoading && accounts.length === 0 && (
            <p className={styles.muted}>
              No ad accounts found. Try “Refresh accounts”.
            </p>
          )}
          {accounts.map((acc) => (
            <label key={acc.account_id} className={styles.accountRow}>
              <input
                type="checkbox"
                checked={acc.enabled}
                onChange={(e) => handleToggle(acc.account_id, e.target.checked)}
              />
              <span className={styles.accountName}>{acc.name}</span>
              <span className={styles.accountMeta}>
                {acc.currency} · {acc.account_status}
              </span>
            </label>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className={styles.cardActions}>
        {!integration.connected ? (
          <button
            className={styles.primaryBtn}
            onClick={handleConnect}
            disabled={!integration.configured || busy}
          >
            {busy ? "Connecting…" : "Connect"}
          </button>
        ) : (
          <>
            <button
              className={styles.secondaryBtn}
              onClick={handleRefresh}
              disabled={busy}
            >
              {busy ? "Working…" : "Refresh accounts"}
            </button>
            <button
              className={styles.dangerBtn}
              onClick={handleDisconnect}
              disabled={busy}
            >
              Disconnect
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Page body
// ============================================================================

const PLATFORM_LABEL_FALLBACK: Record<string, string> = {
  google_ads: "Google Ads",
  meta_ads: "Meta Ads",
  tiktok_ads: "TikTok Ads",
  reddit_ads: "Reddit Ads",
};

function IntegrationsBody() {
  const params = useSearchParams();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const connected = params.get("connected");
  const status = params.get("status");
  const reason = params.get("reason");

  const load = useCallback(async () => {
    setLoading(true);
    const { data, error } = await listIntegrations();
    if (error) setError(error);
    else {
      setIntegrations(data ?? []);
      setError(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const bannerLabel = connected
    ? PLATFORM_LABEL_FALLBACK[connected] ?? connected
    : "";

  return (
    <div>
      <div className={styles.header}>
        <h1 className={styles.title}>Integrations</h1>
      </div>

      {connected && status === "success" && (
        <div className={`${styles.banner} ${styles.bannerSuccess}`}>
          {bannerLabel} connected successfully.
        </div>
      )}
      {connected && status === "error" && (
        <div className={`${styles.banner} ${styles.bannerError}`}>
          Could not connect {bannerLabel}
          {reason ? `: ${reason}` : "."}
        </div>
      )}

      {loading && <p className={styles.empty}>Loading…</p>}
      {!loading && error && <p className={styles.error}>{error}</p>}

      {!loading && !error && integrations.length === 0 && (
        <div className={styles.emptyState}>
          <p className={styles.emptyTitle}>No integrations available</p>
          <p className={styles.emptyText}>
            Ad-platform integrations are not configured on this server.
          </p>
        </div>
      )}

      {!loading && !error && integrations.length > 0 && (
        <div className={styles.grid}>
          {integrations.map((integration) => (
            <IntegrationCard
              key={integration.platform}
              integration={integration}
              onChanged={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Page export (wraps body in Suspense for useSearchParams)
// ============================================================================

export default function IntegrationsPage() {
  return (
    <Suspense>
      <IntegrationsBody />
    </Suspense>
  );
}
