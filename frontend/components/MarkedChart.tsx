"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  createSeriesMarkers,
  AreaSeries,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import type { EquityPoint, TradeRow } from "@/lib/types";

const COLORS = {
  line: "#4f8cff",
  fillTop: "rgba(79, 140, 255, 0.28)",
  fillBottom: "rgba(79, 140, 255, 0.02)",
  grid: "rgba(255,255,255,0.05)",
  text: "#8b93a7",
  entry: "#4f8cff",
  win: "#34d399",
  loss: "#f87171",
};

function toSeconds(iso: string | null | undefined): UTCTimestamp | null {
  if (!iso) return null;
  const ms = Date.parse(iso.endsWith("Z") ? iso : `${iso}+05:30`);
  return Number.isNaN(ms) ? null : ((ms / 1000) as UTCTimestamp);
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

  useEffect(() => {
    const node = holder.current;
    if (!node) return;

    const chart = createChart(node, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: COLORS.text,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
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

    const resize = () => chart.applyOptions({ width: node.clientWidth });
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(node);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
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

    const markers = trades
      .flatMap((t) => {
        const entry = toSeconds(t.entry_ts);
        const exit = toSeconds(t.exit_ts);
        const net = t.net ?? 0;
        const out = [];
        if (entry !== null) {
          out.push({
            time: entry,
            position: "belowBar" as const,
            color: COLORS.entry,
            shape: (t.side === "PE" ? "arrowDown" : "arrowUp") as "arrowDown" | "arrowUp",
            text: `${t.side ?? ""} ${t.strike ?? ""} @ ${t.entry_prem?.toFixed(1) ?? "?"}`,
          });
        }
        if (exit !== null) {
          out.push({
            time: exit,
            position: "aboveBar" as const,
            color: net >= 0 ? COLORS.win : COLORS.loss,
            shape: "circle" as const,
            text: `${t.reason ?? "exit"} ${net >= 0 ? "+" : ""}${Math.round(net)}`,
          });
        }
        return out;
      })
      .sort((a, b) => (a.time as number) - (b.time as number));

    createSeriesMarkers(series, markers);
    chartRef.current?.timeScale().fitContent();
  }, [equity, trades]);

  if (!equity.length) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed border-hairline text-xs text-ink-muted"
        style={{ height }}
      >
        No equity marks yet — the curve starts once an engine is running.
      </div>
    );
  }

  return <div ref={holder} className="w-full" style={{ height }} />;
}
