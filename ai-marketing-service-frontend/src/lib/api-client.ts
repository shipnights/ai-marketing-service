/**
 * Shared API client infrastructure.
 *
 * Single source of truth for:
 *   - API_URL constant
 *   - ApiResponse<T> type
 *   - apiCall<T> fetch wrapper with auth injection, silent token refresh, and error handling
 *
 * All API modules (auth-api.ts, projects-api.ts, etc.) import from here.
 */

import { STORAGE_KEYS } from "./storage";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

// ============================================================================
// Types
// ============================================================================

export interface CreditError {
  error: "INSUFFICIENT_CREDITS";
  message: string;
  required: number;
  balance: number;
}

export interface ApiResponse<T> {
  data: T;
  error: string | null;
  status: number;
  creditError?: CreditError;
}

// ============================================================================
// Token refresh (module-level to deduplicate concurrent 401s)
// ============================================================================

let _refreshPromise: Promise<string | null> | null = null;

async function _attemptTokenRefresh(): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise;

  _refreshPromise = (async () => {
    try {
      const refreshToken =
        typeof window !== "undefined"
          ? localStorage.getItem(STORAGE_KEYS.refreshToken)
          : null;
      if (!refreshToken) return null;

      const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (!response.ok) {
        if (typeof window !== "undefined") {
          localStorage.removeItem(STORAGE_KEYS.accessToken);
          localStorage.removeItem(STORAGE_KEYS.refreshToken);
          localStorage.removeItem(STORAGE_KEYS.user);
          localStorage.removeItem(STORAGE_KEYS.team);
          window.location.href = "/auth?mode=login";
        }
        return null;
      }

      const body = await response.json();
      const data =
        body?.status === "success" && body?.data !== undefined
          ? body.data
          : (body?.data ?? body);
      const newAccessToken: string | undefined = data?.accessToken;
      const newRefreshToken: string | undefined = data?.refreshToken;
      if (!newAccessToken) return null;

      localStorage.setItem(STORAGE_KEYS.accessToken, newAccessToken);
      if (newRefreshToken) {
        localStorage.setItem(STORAGE_KEYS.refreshToken, newRefreshToken);
      }
      return newAccessToken;
    } finally {
      _refreshPromise = null;
    }
  })();

  return _refreshPromise;
}

// ============================================================================
// Fetch wrapper
// ============================================================================

/**
 * Generic fetch wrapper with:
 * - Automatic Bearer token injection from localStorage
 * - 401 → silent token refresh → retry once
 * - 402 (insufficient credits) → returns { creditError }
 * - 422 validation errors → joined error string
 * - Success envelope unwrapping: { status: "success", data: ... } → returns data
 * - Non-JSON / HTML error page detection
 */
export async function apiCall<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const makeHeaders = (token?: string | null): Record<string, string> => {
    const base: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    const stored =
      token ??
      (typeof window !== "undefined"
        ? localStorage.getItem(STORAGE_KEYS.accessToken)
        : null);
    if (stored) base["Authorization"] = `Bearer ${stored}`;
    return base;
  };

  try {
    const url = `${API_URL}${endpoint}`;
    const response = await fetch(url, { ...options, headers: makeHeaders() });

    // Detect non-JSON responses
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) {
      const text = await response.text();
      const isHtml =
        text.trimStart().startsWith("<!") || text.trimStart().startsWith("<?");
      const message = isHtml
        ? "The server returned an HTML page instead of JSON. Ensure the API backend is running and NEXT_PUBLIC_API_URL is correct (e.g. http://localhost:8000)."
        : `Unexpected response (${response.status}): ${text.slice(0, 100)}`;
      return { data: null as unknown as T, error: message, status: response.status };
    }

    const body = await response.json();

    if (!response.ok) {
      const detail = body?.detail;

      // 401 → attempt silent refresh → retry
      if (response.status === 401 && typeof window !== "undefined") {
        const newToken = await _attemptTokenRefresh();
        if (newToken) {
          try {
            const retryResponse = await fetch(url, {
              ...options,
              headers: makeHeaders(newToken),
            });
            const ct = retryResponse.headers.get("content-type") ?? "";
            if (ct.includes("application/json")) {
              const retryBody = await retryResponse.json();
              if (retryResponse.ok) {
                const data: T =
                  retryBody?.status === "success" && retryBody?.data !== undefined
                    ? retryBody.data
                    : (retryBody?.data ?? retryBody);
                return { data, error: null, status: retryResponse.status };
              }
            }
          } catch {
            // retry failed — fall through
          }
        }
        const msg =
          (typeof detail === "string" && detail) || `HTTP ${response.status}`;
        return { data: null as unknown as T, error: msg, status: response.status };
      }

      // 402 → insufficient credits
      if (
        response.status === 402 &&
        detail &&
        typeof detail === "object" &&
        detail.error === "INSUFFICIENT_CREDITS"
      ) {
        return {
          data: null as unknown as T,
          error: detail.message || "Insufficient AI credits",
          status: response.status,
          creditError: detail as CreditError,
        };
      }

      // 422 → validation errors array
      if (response.status === 422 && body?.errors && Array.isArray(body.errors)) {
        const msg = body.errors
          .map((e: { field?: string; message?: string }) => e.message || String(e))
          .join("; ");
        return { data: body as T, error: msg, status: response.status };
      }

      const errorMessage =
        (typeof detail === "string" && detail) ||
        (typeof body?.message === "string" && body.message) ||
        `HTTP ${response.status}`;
      return { data: body as T, error: errorMessage, status: response.status };
    }

    const data: T =
      body?.status === "success" && body?.data !== undefined
        ? body.data
        : (body?.data ?? body);
    return { data, error: null, status: response.status };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    const isJsonError =
      message.includes("is not valid JSON") || message.includes("Unexpected token");
    const errorMessage = isJsonError
      ? "The server returned an unexpected response. Ensure NEXT_PUBLIC_API_URL is set (e.g. in .env.local: NEXT_PUBLIC_API_URL=http://localhost:8000)."
      : message;
    return { data: null as unknown as T, error: errorMessage, status: 0 };
  }
}
