"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  createSeriesMarkers,
  AreaSeries,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type UTCTimestamp,
} from "lightweight-charts";

import type { EquityPoint, TradeRow } from "@/lib/types";

const COLORS = {
  line: "#4f8cff",
  fillTop: "rgba(79, 140, 255, 0.28)",
  fillBottom: "rgba(79, 140, 255, 0.02)",
};

/** A theme colour from the stylesheet, so the chart matches light and dark. */
function themeColor(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const IST_SECONDS = 5.5 * 3600;

/**
 * Seconds for the chart, as Indian wall-clock time.
 *
 * Lightweight Charts draws every time in UTC, so a real timestamp put a 14:16
 * trade at "08:46". The NIFTY chart's bars are shifted to IST on the server;
 * these are shifted here the same way, so the axis and the crosshair read IST.
 */
function toSeconds(iso: string | null | undefined): UTCTimestamp | null {
  if (!iso) return null;
  const zoned = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const ms = Date.parse(zoned ? iso : `${iso}+05:30`);
  return Number.isNaN(ms) ? null : ((Math.round(ms / 1000) + IST_SECONDS) as UTCTimestamp);
}

function rupees(n: number): string {
  return `${n >= 0 ? "+" : "−"}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;
}

/**
 * Our own equity curve, with a marker wherever a trade was placed.
 *
 * TradingView's hosted widget renders in an iframe we cannot draw into, so the
 * fills go here instead: this uses TradingView's open-source charting library
 * against our own data, which is the only way to put our executions on a chart.
 */
export function MarkedChart({
  equity,
  trades,
  height = 300,
}: {
  equity: EquityPoint[];
  trades: TradeRow[];
  height?: number;
}) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<UTCTimestamp> | null>(null);
  // Bumped once the chart exists, so data that arrived first is drawn then.
  const [ready, setReady] = useState(0);

  useEffect(() => {
    const node = holder.current;
    if (!node) return;

    const chart = createChart(node, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: themeColor("--text-muted", "#8b93a7"),
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: themeColor("--gridline", "rgba(255,255,255,0.05)") },
        horzLines: { color: themeColor("--gridline", "rgba(255,255,255,0.05)") },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      handleScale: { axisPressedMouseMove: false },
    });

    const series = chart.addSeries(AreaSeries, {
      lineColor: COLORS.line,
      topColor: COLORS.fillTop,
      bottomColor: COLORS.fillBottom,
      lineWidth: 2,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });

    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = createSeriesMarkers(series, []) as ISeriesMarkersPluginApi<UTCTimestamp>;
    setReady((n) => n + 1);

    const resize = () => chart.applyOptions({ width: node.clientWidth });
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(node);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    // Lightweight Charts rejects duplicate or unsorted timestamps outright.
    const byTime = new Map<number, number>();
    for (const point of equity) {
      const t = toSeconds(point.ts);
      if (t !== null) byTime.set(t, point.equity);
    }
    const data = Array.from(byTime.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time: time as UTCTimestamp, value }));

    series.setData(data);

    // Only this curve's own period: a trade from another day would be pinned
    // to the nearest edge of the chart and read as if it happened there.
    const first = data.length ? (data[0].time as number) - 120 : 0;
    const last = data.length ? (data[data.length - 1].time as number) + 120 : 0;
    // Every order in the brand gold, which stands clear of the blue equity line:
    // a BUY arrow under the line where the option was bought, a SELL arrow over
    // it where it was sold, with the price, why it was sold and the result.
    const gold = themeColor("--brand", "#d4af37");
    // On a phone-width chart the full labels would take most of its width.
    const compact = (chartRef.current?.timeScale().width() ?? 1000) < 560;
    const markers = trades
      .flatMap((t) => {
        const entry = toSeconds(t.entry_ts);
        const exit = toSeconds(t.exit_ts);
        const out = [];
        if (entry !== null) {
          out.push({
            time: entry,
            position: "belowBar" as const,
            color: gold,
            shape: "arrowUp" as const,
            text: compact
              ? `BUY @ ${t.entry_prem?.toFixed(2) ?? "?"}`
              : `BUY ${t.side ?? ""} ${t.strike ?? ""} @ ${t.entry_prem?.toFixed(2) ?? "?"}`,
          });
        }
        if (exit !== null) {
          out.push({
            time: exit,
            position: "aboveBar" as const,
            color: gold,
            shape: "arrowDown" as const,
            text: compact
              ? `SELL${t.net != null ? ` ${rupees(t.net)}` : ` @ ${t.exit_prem?.toFixed(2) ?? "?"}`}`
              : `SELL @ ${t.exit_prem?.toFixed(2) ?? "?"}` +
                (t.reason ? ` · ${t.reason}` : "") +
                (t.net != null ? ` · ${rupees(t.net)}` : ""),
          });
        }
        return out;
      })
      .filter((m) => (m.time as number) >= first && (m.time as number) <= last)
      .sort((a, b) => (a.time as number) - (b.time as number));

    // One markers layer, updated in place: a new layer on every refresh used
    // to pile duplicate markers on top of each other.
    markersRef.current?.setMarkers(markers);
    const scale = chartRef.current?.timeScale();
    if (scale) {
      scale.fitContent();
      // A sale at 15:10 is the curve's last point, and its label is centred on
      // it: leave room after the line (and a little before it) so the label is
      // never cut off at the edge.
      const width = scale.width();
      const n = data.length;
      if (markers.length && n > 1 && width > 0) {
        // Half the widest label, at about 6.5 px a character, in bars of room.
        const half = Math.max(...markers.map((m) => m.text.length)) * 3.3 + 10;
        const after = Math.min(Math.ceil((half * (n + 2)) / Math.max(width - half, 1)), Math.ceil(n * 0.5));
        const before = Math.min(Math.ceil((half * n) / width / 2), Math.ceil(n * 0.1));
        scale.setVisibleLogicalRange({ from: -before, to: n - 1 + after });
      }
    }
  }, [equity, trades, ready]);

  // The chart's container is always mounted. It used to be rendered only once
  // the data had arrived, but the chart is built when the component first
  // mounts — so on the deck, where the data comes a moment later, the chart
  // was never built and the box stayed empty.
  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={holder} className="h-full w-full" />
      {!equity.length && (
        <div className="absolute inset-0 flex items-center justify-center rounded-md border border-dashed border-hairline text-xs text-ink-muted">
          No equity marks yet — the curve starts once an engine is running.
        </div>
      )}
    </div>
  );
}
