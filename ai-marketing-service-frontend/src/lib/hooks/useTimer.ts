"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface UseTimerOptions {
  /** Duration in seconds. */
  durationSeconds: number;
  /** Called when the timer reaches zero. */
  onExpire?: () => void;
  /** Start the timer immediately on mount. Default: true. */
  autoStart?: boolean;
}

interface UseTimerResult {
  /** Remaining seconds (counts down to 0). */
  remaining: number;
  /** Whether the timer is actively counting down. */
  isRunning: boolean;
  /** Whether the timer has reached zero. */
  isExpired: boolean;
  start: () => void;
  pause: () => void;
  reset: () => void;
  /** MM:SS formatted string. */
  formatted: string;
}

/**
 * Countdown timer hook.
 *
 * @example
 * const { remaining, formatted, isExpired } = useTimer({
 *   durationSeconds: 1800,   // 30 minutes
 *   onExpire: () => router.push("/timeout"),
 * });
 */
export function useTimer({
  durationSeconds,
  onExpire,
  autoStart = true,
}: UseTimerOptions): UseTimerResult {
  const [remaining, setRemaining] = useState(durationSeconds);
  const [isRunning, setIsRunning] = useState(false);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onExpireRef = useRef(onExpire);
  onExpireRef.current = onExpire;

  const clearTimer = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const start = useCallback(() => {
    setIsRunning((prev) => {
      if (prev) return prev; // already running
      return true;
    });
  }, []);

  const pause = useCallback(() => {
    clearTimer();
    setIsRunning(false);
  }, [clearTimer]);

  const reset = useCallback(() => {
    clearTimer();
    setRemaining(durationSeconds);
    setIsRunning(false);
  }, [clearTimer, durationSeconds]);

  // Start/stop the interval whenever isRunning changes.
  useEffect(() => {
    if (!isRunning) return;

    intervalRef.current = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          clearTimer();
          setIsRunning(false);
          onExpireRef.current?.();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return clearTimer;
  }, [isRunning, clearTimer]);

  // Auto-start on mount.
  useEffect(() => {
    if (autoStart) start();
    return clearTimer;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  const formatted = `${mm}:${ss}`;

  return {
    remaining,
    isRunning,
    isExpired: remaining === 0,
    start,
    pause,
    reset,
    formatted,
  };
}
