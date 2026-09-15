"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiGet } from "@/lib/client-api";
import type { HealthDetail, NewsPayload, TickerPayload } from "@/lib/types";

/**
 * Polls one endpoint on an interval and keeps the last good value.
 *
 * A failed poll never blanks the panel — a desk that flickers to empty on a
 * dropped request is harder to trust than one showing a value a few seconds
 * stale, so staleness is surfaced instead of the data disappearing.
 */
function usePoll<T>(path: string, intervalMs: number, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const inFlight = useRef(false);

  const tick = useCallback(async () => {
    if (inFlight.current) return; // never stack requests on a slow endpoint
    inFlight.current = true;
    try {
      setData(await apiGet<T>(path));
      setUpdatedAt(Date.now());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      inFlight.current = false;
    }
  }, [path]);

  useEffect(() => {
    if (!enabled) return;
    void tick();
    timer.current = setInterval(() => void tick(), intervalMs);
    return () => {
      if (timer.current) clearInterval(timer.current);
      timer.current = null;
    };
  }, [tick, intervalMs, enabled]);

  return { data, error, updatedAt, refresh: tick };
}

/** The index ticker moves every second, so it polls every second. */
export function useTicker() {
  return usePoll<TickerPayload>("/api/ticker", 1000);
}

export function useHealth() {
  return usePoll<HealthDetail>("/api/health/detail", 15_000);
}

export function useNews() {
  return usePoll<NewsPayload>("/api/news", 120_000);
}
