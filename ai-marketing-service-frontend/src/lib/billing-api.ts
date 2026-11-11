/**
 * Billing API module — plans/credit-pack catalog, subscription, credits ledger,
 * and Stripe checkout / customer-portal redirects.
 */

import { apiCall, type ApiResponse } from "./api-client";

export type { ApiResponse };

// ============================================================================
// Types
// ============================================================================

export type PlanKey = "solo" | "team" | "agency";
export type BillingInterval = "monthly" | "annual";

export interface PlanLimits {
  projects: number;
  social_accounts: number;
  ad_accounts: number;
  team_members: number;
  storage_gb: number;
}

export interface Plan {
  key: PlanKey;
  name: string;
  monthly_credits: number;
  price_monthly_usd: number;
  price_annual_usd: number;
  limits: PlanLimits;
  features: string[];
  configured: boolean;
}

export interface Pack {
  key: string;
  name: string;
  credits: number;
  price_usd: number;
  configured: boolean;
}

export interface BillingCatalog {
  plans: Plan[];
  credit_packs: Pack[];
  stripe_configured: boolean;
  publishable_key: string | null;
}

export interface Subscription {
  plan: string;
  status: string;
  interval: string | null;
  current_period_end: number | null;
  cancel_at_period_end: boolean;
  credits_balance: number;
  has_customer: boolean;
}

export interface LedgerEntry {
  delta: number;
  balance_after: number;
  reason: string;
  source: string;
  created_at: string;
}

export interface CreditsInfo {
  balance: number;
  ledger: LedgerEntry[];
}

// ============================================================================
// API functions
// ============================================================================

export async function getPlans() {
  return apiCall<BillingCatalog>("/api/v1/billing/plans");
}

export async function getSubscription() {
  return apiCall<Subscription>("/api/v1/billing/subscription");
}

export async function getCredits() {
  return apiCall<CreditsInfo>("/api/v1/billing/credits");
}

export async function startCheckout(planKey: PlanKey, interval: BillingInterval) {
  return apiCall<{ url: string }>("/api/v1/billing/checkout", {
    method: "POST",
    body: JSON.stringify({ plan_key: planKey, interval }),
  });
}

export async function startCreditCheckout(packKey: string) {
  return apiCall<{ url: string }>("/api/v1/billing/credits/checkout", {
    method: "POST",
    body: JSON.stringify({ pack_key: packKey }),
  });
}

export async function openPortal() {
  return apiCall<{ url: string }>("/api/v1/billing/portal", {
    method: "POST",
  });
}
