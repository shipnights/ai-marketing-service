/**
 * Base API helpers (health check, service info).
 * All domain-specific API modules live alongside this file (projects-api.ts, etc.).
 */

export { API_URL, type ApiResponse } from "./api-client";
import { apiCall } from "./api-client";

export async function checkHealth() {
  return apiCall<{ status: string }>("/health");
}

export async function getServiceInfo() {
  return apiCall<{ service: string; version: string }>("/");
}
