/**
 * Integrations API module — ad-platform connections (OAuth) and ad-account management.
 */

import { apiCall, type ApiResponse } from "./api-client";

export type { ApiResponse };

// ============================================================================
// Types
// ============================================================================

export type AdPlatform = "google_ads" | "meta_ads" | "tiktok_ads" | "reddit_ads";

export interface Integration {
  platform: AdPlatform;
  label: string;
  configured: boolean;
  supports_management: boolean;
  connected: boolean;
  connection_status: string | null;
  last_error: string | null;
  connected_at: string | null;
  account_count: number;
  enabled_account_count: number;
}

export interface AdAccount {
  platform: AdPlatform;
  account_id: string;
  name: string;
  currency: string;
  account_status: string;
  enabled: boolean;
  last_synced_at: string | null;
}

// ============================================================================
// API functions
// ============================================================================

export async function listIntegrations() {
  return apiCall<Integration[]>("/api/v1/integrations");
}

export async function getConnectUrl(platform: AdPlatform) {
  return apiCall<{ authorize_url: string }>(
    `/api/v1/integrations/${platform}/connect`
  );
}

export async function listPlatformAccounts(platform: AdPlatform) {
  return apiCall<AdAccount[]>(`/api/v1/integrations/${platform}/accounts`);
}

export async function refreshPlatformAccounts(platform: AdPlatform) {
  return apiCall<{ discovered: number }>(
    `/api/v1/integrations/${platform}/accounts/refresh`,
    { method: "POST" }
  );
}

export async function setAccountEnabled(
  platform: AdPlatform,
  accountId: string,
  enabled: boolean
) {
  return apiCall<AdAccount>(
    `/api/v1/integrations/${platform}/accounts/${accountId}?enabled=${enabled}`,
    { method: "PATCH" }
  );
}

export async function disconnectPlatform(platform: AdPlatform) {
  return apiCall<{ platform: AdPlatform }>(`/api/v1/integrations/${platform}`, {
    method: "DELETE",
  });
}
