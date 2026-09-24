"use client";

import { useEffect, useRef, useState } from "react";

import { apiGet } from "@/lib/client-api";
import type { NiftyChartPayload } from "@/lib/types";

/**
 * Today's NIFTY 50 in one-minute candles.
 *
 * Drawn from Angel One's own feed through a running engine — the candles the
 * strategy trades on — and from a public feed when no engine is up. TradingView's
 * free widget does not reliably carry NSE's index, so this is the default view.
 * The chart library loads with the chart, not with the page.
 */
export function NiftyChart({ height = 380 }: { height?: number }) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<{ remove: () => void; timeScale: () => { fitContent: () => void } } | null>(
    null,
  );
  const fitted = useRef(false);
  const seriesRef = useRef<{ setData: (d: unknown[]) => void } | null>(null);
  const [data, setData] = useState<NiftyChartPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await apiGet<NiftyChartPayload>("/market/nifty");
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "chart unavailable");
      }
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // Create the chart once, lazily.
  useEffect(() => {
    let disposed = false;
    let observer: ResizeObserver | null = null;
    void (async () => {
      const node = holder.current;
      if (!node) return;
      const lw = await import("lightweight-charts");
      if (disposed) return;
      const styles = getComputedStyle(document.documentElement);
      const text = styles.getPropertyValue("--text-muted").trim() || "#8a877e";
      const grid = styles.getPropertyValue("--gridline").trim() || "#24241f";
      const chart = lw.createChart(node, {
        height: node.clientHeight,
        layout: {
          background: { type: lw.ColorType.Solid, color: "transparent" },
          textColor: text,
          fontSize: 11,
          attributionLogo: false,
        },
        grid: { vertLines: { color: grid }, horzLines: { color: grid } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
        crosshair: { mode: lw.CrosshairMode.Normal },
      });
      const series = chart.addSeries(lw.CandlestickSeries, {
        upColor: "#2fbf4f",
        downColor: "#e66767",
        wickUpColor: "#2fbf4f",
        wickDownColor: "#e66767",
        borderVisible: false,
        priceFormat: { type: "price", precision: 2, minMove: 0.05 },
      });
      chartRef.current = chart;
      seriesRef.current = series as unknown as { setData: (d: unknown[]) => void };
      const resize = () => chart.applyOptions({ width: node.clientWidth, height: node.clientHeight });
      resize();
      observer = new ResizeObserver(resize);
      observer.observe(node);
    })();
    return () => {
      disposed = true;
      observer?.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Push data whenever it or the chart arrives.
  useEffect(() => {
    const push = () => {
      if (!seriesRef.current || !data?.bars?.length) return false;
      seriesRef.current.setData(
        data.bars.map((b) => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })),
      );
      // The whole session on first sight; after that, leave any zoom alone.
      if (!fitted.current) {
        chartRef.current?.timeScale().fitContent();
        fitted.current = true;
      }
      return true;
    };
    if (push()) return;
    const retry = setInterval(() => push() && clearInterval(retry), 200);
    return () => clearInterval(retry);
  }, [data]);

  const last = data?.bars?.length ? data.bars[data.bars.length - 1] : null;
  const first = data?.bars?.length ? data.bars[0] : null;
  const change = last && first ? last.c - first.o : null;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-semibold tabular-nums text-ink">
            {last ? last.c.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—"}
          </span>
          {change !== null && (
            <span className={`text-xs tabular-nums ${change >= 0 ? "text-profit" : "text-loss"}`}>
              {change >= 0 ? "+" : ""}
              {change.toFixed(2)} today
            </span>
          )}
        </div>
        <span className={`text-2xs ${data?.stale ? "text-warning" : "text-ink-muted"}`}>
          {data?.label ?? (error ? `unavailable — ${error}` : "loading…")}
        </span>
      </div>
      <div
        className="relative h-[280px] sm:h-[var(--chart-h)]"
        style={{ ["--chart-h" as string]: `${height}px` }}
      >
        <div ref={holder} className="absolute inset-0" />
        {data && !data.bars.length && (
          <div className="absolute inset-0 flex items-center justify-center rounded-md border border-dashed border-hairline px-4 text-center text-xs text-ink-muted">
            No NIFTY candles yet — the market may be closed, and no source is reachable right now.
          </div>
        )}
      </div>
    </div>
  );
}
