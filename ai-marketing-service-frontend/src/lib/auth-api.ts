/**
 * Auth API module — registration wizard (steps 1–4), login, refresh, logout,
 * email verification, and password reset.
 *
 * All request/response shapes match the backend exactly (snake_case bodies,
 * camelCase response fields from success_response wrapper).
 */

import { apiCall, type ApiResponse } from "./api-client";
import { STORAGE_KEYS } from "./storage";

export type { ApiResponse };

// ============================================================================
// Shared types
// ============================================================================

export interface TokenPair {
  accessToken: string;
  refreshToken: string;
  userId: string;
  teamId: string;
}

// ============================================================================
// Step 1 — Account credentials
// ============================================================================

export interface RegisterStep1Response {
  tempToken: string;
  userId: string;
}

export async function registerStep1(data: { email: string; password: string }) {
  return apiCall<RegisterStep1Response>("/api/v1/auth/register/step-1", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ============================================================================
// Step 2 — Profile info
// ============================================================================

export interface RegisterStep2Request {
  temp_token: string;
  full_name: string;
  role?: string;
}

export interface RegisterStep2Response {
  tempToken: string;
}

export async function registerStep2(data: RegisterStep2Request) {
  return apiCall<RegisterStep2Response>("/api/v1/auth/register/step-2", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ============================================================================
// Step 3 — Team creation
// ============================================================================

export interface RegisterStep3Request {
  temp_token: string;
  team_name: string;
}

export interface RegisterStep3Response {
  tempToken: string;
  teamId: string;
}

export async function registerStep3(data: RegisterStep3Request) {
  return apiCall<RegisterStep3Response>("/api/v1/auth/register/step-3", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ============================================================================
// Step 4 — Finalize
// ============================================================================

export async function registerStep4(temp_token: string) {
  return apiCall<TokenPair>("/api/v1/auth/register/step-4", {
    method: "POST",
    body: JSON.stringify({ temp_token }),
  });
}

// ============================================================================
// Login
// ============================================================================

export async function login(data: { email: string; password: string }) {
  return apiCall<TokenPair>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

/** Store tokens in localStorage after a successful login or register. */
export function storeAuthSession(data: TokenPair): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEYS.accessToken, data.accessToken);
  localStorage.setItem(STORAGE_KEYS.refreshToken, data.refreshToken);
}

/** Clear all auth data from localStorage (logout / session expiry). */
export function clearAuthSession(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(STORAGE_KEYS.accessToken);
  localStorage.removeItem(STORAGE_KEYS.refreshToken);
  localStorage.removeItem(STORAGE_KEYS.user);
  localStorage.removeItem(STORAGE_KEYS.team);
}

// ============================================================================
// Refresh
// ============================================================================

export async function refreshToken(token: string) {
  return apiCall<TokenPair>("/api/v1/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: token }),
  });
}

// ============================================================================
// Logout
// ============================================================================

export async function logout(token: string) {
  return apiCall<null>("/api/v1/auth/logout", {
    method: "POST",
    body: JSON.stringify({ refresh_token: token }),
  });
}

// ============================================================================
// Email verification
// ============================================================================

export async function verifyEmail(token: string) {
  return apiCall<null>("/api/v1/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

// ============================================================================
// Password reset
// ============================================================================

export async function requestPasswordReset(email: string) {
  return apiCall<null>("/api/v1/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function resetPassword(token: string, new_password: string) {
  return apiCall<null>("/api/v1/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password }),
  });
}
