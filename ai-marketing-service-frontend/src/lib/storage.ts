/**
 * Typed localStorage key constants.
 * Use these instead of raw strings to prevent typos and enable autocomplete.
 */

export const STORAGE_KEYS = {
  accessToken: "accessToken",
  refreshToken: "refreshToken",
  user: "user",
  team: "team",
} as const;

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS];
