"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface UsePollOptions<T> {
  /** Async function that fetches the current state. */
  fetcher: () => Promise<T>;
  /** Return true to stop polling (task is complete or terminal error). */
  isDone: (data: T) => boolean;
  /** Polling interval in milliseconds. Default: 3000. */
  intervalMs?: number;
  /** Max number of poll attempts before giving up. Default: 60. */
  maxAttempts?: number;
  /** Called when isDone returns true. */
  onDone?: (data: T) => void;
  /** Called when the poll is abandoned (maxAttempts exceeded). */
  onTimeout?: () => void;
  /** Start polling immediately on mount. Default: true. */
  enabled?: boolean;
}

interface UsePollResult<T> {
  data: T | null;
  error: string | null;
  attempts: number;
  isPolling: boolean;
  /** Manually restart polling (e.g. after a user action). */
  restart: () => void;
  /** Stop polling without triggering onTimeout. */
  stop: () => void;
}

/**
 * Generic long-polling hook.
 *
 * Repeatedly calls `fetcher` every `intervalMs` ms until `isDone` returns
 * true, `maxAttempts` is exceeded, or `stop()` is called.
 *
 * @example
 * const { data, isPolling } = usePoll({
 *   fetcher: () => getJobStatus(jobId),
 *   isDone: (d) => d.status === "done" || d.status === "failed",
 *   onDone: (d) => console.log("Job finished", d),
 * });
 */
export function usePoll<T>({
  fetcher,
  isDone,
  intervalMs = 3000,
  maxAttempts = 60,
  onDone,
  onTimeout,
  enabled = true,
}: UsePollOptions<T>): UsePollResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0);
  const [isPolling, setIsPolling] = useState(false);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptsRef = useRef(0);
  const activeRef = useRef(false);

  const stop = useCallback(() => {
    activeRef.current = false;
    setIsPolling(false);
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const tick = useCallback(async () => {
    if (!activeRef.current) return;

    attemptsRef.current += 1;
    setAttempts(attemptsRef.current);

    if (attemptsRef.current > maxAttempts) {
      stop();
      onTimeout?.();
      return;
    }

    try {
      const result = await fetcher();
      if (!activeRef.current) return;
      setData(result);
      setError(null);

      if (isDone(result)) {
        stop();
        onDone?.(result);
        return;
      }
    } catch (err) {
      if (!activeRef.current) return;
      setError(err instanceof Error ? err.message : "Poll error");
    }

    if (activeRef.current) {
      timerRef.current = setTimeout(tick, intervalMs);
    }
  }, [fetcher, isDone, intervalMs, maxAttempts, onDone, onTimeout, stop]);

  const restart = useCallback(() => {
    stop();
    attemptsRef.current = 0;
    setAttempts(0);
    setError(null);
    activeRef.current = true;
    setIsPolling(true);
    timerRef.current = setTimeout(tick, 0);
  }, [stop, tick]);

  useEffect(() => {
    if (!enabled) return;
    restart();
    return stop;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return { data, error, attempts, isPolling, restart, stop };
}
