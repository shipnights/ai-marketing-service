/**
 * Ads reporting API module — cross-platform performance overview, campaigns, and sync.
 *
 * Also exports shared formatting helpers (fmtCurrency, fmtNumber, fmtPct, fmtMultiple)
 * used across the ad-manager UI.
 */

import { apiCall, type ApiResponse } from "./api-client";
import type { AdAccount, AdPlatform } from "./integrations-api";

export type { ApiResponse, AdAccount, AdPlatform };

// ============================================================================
// Types
// ============================================================================

export interface Metrics {
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  conversion_value: number;
  reach: number;
  video_views: number;
  ctr: number; // fraction (0.02 = 2%)
  cpc: number; // currency
  cpm: number; // currency
  cpa: number; // currency
  cvr: number; // fraction
  roas: number; // multiple (5.0 = 5×)
}

export interface TimeseriesPoint extends Metrics {
  date: string;
}

export interface AdsOverview {
  since: string;
  until: string;
  totals: Metrics;
  by_platform: Partial<Record<AdPlatform, Metrics>>;
  timeseries: TimeseriesPoint[];
}

export interface CampaignRow extends Metrics {
  platform: AdPlatform;
  account_id: string;
  campaign_id: string;
  name: string;
  currency: string;
}

export interface CampaignsParams {
  since?: string;
  until?: string;
  sort_by?: string;
  limit?: number;
}

export interface SyncResult {
  accounts_synced: number;
  rows: number;
  results: unknown[];
}

// ============================================================================
// API functions
// ============================================================================

export async function getOverview(since?: string, until?: string) {
  const params = new URLSearchParams();
  if (since) params.set("since", since);
  if (until) params.set("until", until);
  const qs = params.toString();
  return apiCall<AdsOverview>(`/api/v1/ads/overview${qs ? `?${qs}` : ""}`);
}

export async function getCampaigns(params: CampaignsParams = {}) {
  const search = new URLSearchParams();
  if (params.since) search.set("since", params.since);
  if (params.until) search.set("until", params.until);
  if (params.sort_by) search.set("sort_by", params.sort_by);
  if (params.limit != null) search.set("limit", String(params.limit));
  const qs = search.toString();
  return apiCall<CampaignRow[]>(`/api/v1/ads/campaigns${qs ? `?${qs}` : ""}`);
}

export async function listAdAccounts() {
  return apiCall<AdAccount[]>("/api/v1/ads/accounts");
}

export async function triggerSync(lookbackDays?: number, platform?: AdPlatform) {
  const params = new URLSearchParams();
  if (lookbackDays != null) params.set("lookback_days", String(lookbackDays));
  if (platform) params.set("platform", platform);
  const qs = params.toString();
  return apiCall<SyncResult>(`/api/v1/ads/sync${qs ? `?${qs}` : ""}`, {
    method: "POST",
  });
}

// ============================================================================
// Formatting helpers
// ============================================================================

export function fmtCurrency(value: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(value) ? value : 0);
}

export function fmtNumber(value: number): string {
  return (Number.isFinite(value) ? value : 0).toLocaleString("en-US");
}

export function fmtPct(fraction: number): string {
  const v = Number.isFinite(fraction) ? fraction : 0;
  return `${(v * 100).toFixed(2)}%`;
}

export function fmtMultiple(value: number): string {
  const v = Number.isFinite(value) ? value : 0;
  return `${v.toFixed(2)}×`;
}

// ============================================================================
// Platform labels
// ============================================================================

export const PLATFORM_LABELS: Record<AdPlatform, string> = {
  google_ads: "Google Ads",
  meta_ads: "Meta Ads",
  tiktok_ads: "TikTok Ads",
  reddit_ads: "Reddit Ads",
};
