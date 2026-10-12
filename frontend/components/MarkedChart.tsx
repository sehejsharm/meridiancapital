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

import type { EquityPoint, Position, TradeRow } from "@/lib/types";

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

function rupees(n: number, paise = true): string {
  const v = Math.abs(n).toLocaleString("en-IN", {
    minimumFractionDigits: paise ? 2 : 0,
    maximumFractionDigits: paise ? 2 : 0,
  });
  return `${n >= 0 ? "+" : "−"}₹${v}`;
}

/** "10:30" from a stored timestamp, as written (the program writes IST). */
function clock(ts: string | null | undefined): string {
  const m = /[T ](\d{2}:\d{2})/.exec(ts ?? "");
  return m ? m[1] : "";
}

/** One order on the chart: when it happened and what it was. */
type Fill = {
  kind: "buy" | "sell";
  at: number;            // the order's own time
  time: number;          // the equity sample it is drawn on
  clock: string;
  contract: string;
  price: number | null;
  open?: boolean;
  net?: number | null;
  reason?: string | null;
  n?: number;            // its place in the day's order of events
  value?: number;        // the curve's value at that sample
};

type Chip = { key: string; left: number; top: number; text: string; tone: "buy" | "win" | "loss"; title: string };

const CHIP_H = 22;
const GAP = 16;          // between the arrow's point and its label

let measurer: CanvasRenderingContext2D | null = null;
function textWidth(text: string): number {
  if (typeof document === "undefined") return text.length * 6.5;
  measurer ??= document.createElement("canvas").getContext("2d");
  if (!measurer) return text.length * 6.5;
  measurer.font = `600 11px ${getComputedStyle(document.body).fontFamily || "system-ui"}`;
  return Math.ceil(measurer.measureText(text).width);
}

/**
 * Where each label goes: under its BUY arrow, over its SELL arrow, kept wholly
 * inside the chart, flipped to the other side when there is no room, and moved
 * clear of any label it would otherwise cover.
 */
function layoutChips(
  fills: Fill[],
  g: {
    x: (t: number) => number | null;
    y: (v: number) => number | null;
    width: number;
    height: number;
    compact: boolean;
  },
): Chip[] {
  if (!g.width || !g.height) return [];
  const placed: { l: number; r: number; t: number; b: number }[] = [];
  const out: Chip[] = [];
  for (const f of fills) {
    if (f.value === undefined) continue;
    const x = g.x(f.time);
    const y = g.y(f.value);
    if (x === null || y === null || x < 0 || x > g.width) continue;
    const price = f.price != null ? f.price.toFixed(2) : "?";
    const text =
      f.kind === "buy"
        ? g.compact
          ? `${f.n}. BUY ${price}`
          : `${f.n}. BUY ${f.contract} @ ${price} · ${f.clock}${f.open ? " · open" : ""}`
        : g.compact
          ? `${f.n}. SELL ${f.net != null ? rupees(f.net, false) : price}`
          : `${f.n}. SELL @ ${price} · ${f.clock}${f.net != null ? ` · ${rupees(f.net)}` : ""}`;
    const title =
      f.kind === "buy"
        ? `Bought ${f.contract} at ${price} at ${f.clock}${f.open ? " — still held" : ""}`
        : `Sold ${f.contract} at ${price} at ${f.clock}${f.reason ? ` (${f.reason})` : ""}` +
          (f.net != null ? `, ${rupees(f.net)} after charges` : "");
    const w = textWidth(text) + 16;
    const left = Math.min(Math.max(x - w / 2, 2), Math.max(g.width - w - 2, 2));
    // BUY reads under the line, SELL over it; the other side if it won't fit.
    const below = y + GAP;
    const above = y - GAP - CHIP_H;
    const fits = (t: number) => t >= 2 && t + CHIP_H <= g.height - 2;
    const prefer = f.kind === "buy" ? [below, above] : [above, below];
    let top = fits(prefer[0]) ? prefer[0] : fits(prefer[1]) ? prefer[1] : Math.min(Math.max(prefer[0], 2), g.height - CHIP_H - 2);
    // Step away from labels already placed, in the direction it was going.
    const step = top >= y ? CHIP_H + 4 : -(CHIP_H + 4);
    for (let i = 0; i < 8; i++) {
      const hit = placed.some((p) => left < p.r + 4 && left + w > p.l - 4 && top < p.b + 2 && top + CHIP_H > p.t - 2);
      if (!hit) break;
      const next = top + step;
      if (!fits(next)) break;
      top = next;
    }
    placed.push({ l: left, r: left + w, t: top, b: top + CHIP_H });
    out.push({
      key: `${f.kind}-${f.at}`,
      left,
      top,
      text,
      title,
      tone: f.kind === "buy" ? "buy" : (f.net ?? 0) >= 0 ? "win" : "loss",
    });
  }
  return out;
}

const TONE: Record<Chip["tone"], string> = {
  buy: "border-brand/70 text-brand",
  win: "border-profit/70 text-profit",
  loss: "border-loss/70 text-loss",
};

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
  position = null,
  height = 300,
}: {
  equity: EquityPoint[];
  trades: TradeRow[];
  /** The open position, from the live snapshot: its BUY shows the moment it fills,
   *  not only once the trade has closed and reached the blotter. */
  position?: Position | null;
  height?: number;
}) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<UTCTimestamp> | null>(null);
  // Bumped once the chart exists, so data that arrived first is drawn then.
  const [ready, setReady] = useState(0);
  const [fills, setFills] = useState<Fill[]>([]);
  // Bumped whenever the chart moves, so the labels are placed again.
  const [, setLayoutTick] = useState(0);
  const [hoverTime, setHoverTime] = useState<number | null>(null);

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
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
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
    const times = data.map((d) => d.time as number);
    // A fill sits on the equity sample nearest its order (samples are a minute
    // apart), so it is drawn exactly where the order happened on the line.
    const snap = (t: number): number => {
      if (!times.length) return t;
      let lo = 0;
      let hi = times.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (times[mid] < t) lo = mid + 1;
        else hi = mid;
      }
      const after = times[lo];
      const before = lo > 0 ? times[lo - 1] : after;
      return Math.abs(t - before) <= Math.abs(after - t) ? before : after;
    };
    // Tested on the order's own time, before it is snapped: snapping first put
    // every other day's trades on the curve's first sample, piled at its edge.
    const inPeriod = (t: number | null): t is number => t !== null && data.length > 0 && t >= first && t <= last;

    const out: Fill[] = [];
    const seen = new Set<string>();
    for (const t of trades) {
      const entry = toSeconds(t.entry_ts);
      const exit = toSeconds(t.exit_ts);
      const net = t.net_angel ?? t.net;
      seen.add((t.entry_ts ?? "").replace(" ", "T").slice(0, 16));
      if (inPeriod(entry)) {
        out.push({
          kind: "buy", at: entry, time: snap(entry), clock: clock(t.entry_ts),
          contract: [t.side, t.strike].filter((v) => v != null).join(" "),
          price: t.entry_prem, open: !t.exit_ts,
        });
      }
      if (inPeriod(exit)) {
        out.push({
          kind: "sell", at: exit, time: snap(exit), clock: clock(t.exit_ts),
          contract: [t.side, t.strike].filter((v) => v != null).join(" "),
          price: t.exit_prem, net, reason: t.reason,
        });
      }
    }
    // The position held right now: its BUY from the live snapshot, the moment
    // it fills, not only once the trade has closed and reached the blotter.
    const openAt = position ? toSeconds(position.opened_ts) : null;
    if (position && inPeriod(openAt) && !seen.has(position.opened_ts.replace(" ", "T").slice(0, 16))) {
      out.push({
        kind: "buy", at: openAt, time: snap(openAt), clock: clock(position.opened_ts),
        contract: `${position.side} ${position.strike}`, price: position.entry_premium, open: true,
      });
    }
    // In the order they happened; a BUY and its SELL on one sample, BUY first.
    out.sort((a, b) => a.at - b.at || (a.kind === "buy" ? -1 : 1));
    out.forEach((f, i) => (f.n = i + 1));
    for (const f of out) f.value = byTime.get(f.time);

    // The arrows are the library's; their labels are drawn by us (below), on a
    // backing of their own, because the library writes its text straight over
    // the curve, where a falling line made it unreadable.
    const gold = themeColor("--brand", "#d4af37");
    const profit = themeColor("--profit", "#2fbf4f");
    const loss = themeColor("--loss", "#e66767");
    markersRef.current?.setMarkers(
      out.map((f) => ({
        time: f.time as UTCTimestamp,
        position: f.kind === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
        color: f.kind === "buy" ? gold : (f.net ?? 0) >= 0 ? profit : loss,
        shape: f.kind === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
        size: 1.4,
      })),
    );
    setFills(out);

    const scale = chartRef.current?.timeScale();
    if (scale) {
      scale.fitContent();
      // A little room either side, so an arrow on the curve's first or last
      // sample is not pressed against the edge or the price axis.
      const n = data.length;
      if (out.length && n > 1) {
        const pad = Math.max(2, Math.ceil(n * 0.04));
        scale.setVisibleLogicalRange({ from: -pad, to: n - 1 + pad });
      }
    }
    setLayoutTick((k) => k + 1);
  }, [equity, trades, position, ready]);

  // Labels follow the chart: on a scroll, a zoom or a resize.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const bump = () => setLayoutTick((k) => k + 1);
    chart.timeScale().subscribeVisibleLogicalRangeChange(bump);
    chart.timeScale().subscribeSizeChange(bump);
    const hover = (p: { time?: unknown }) => setHoverTime(typeof p.time === "number" ? p.time : null);
    chart.subscribeCrosshairMove(hover);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(bump);
      chart.timeScale().unsubscribeSizeChange(bump);
      chart.unsubscribeCrosshairMove(hover);
    };
  }, [ready]);

  const chart = chartRef.current;
  const series = seriesRef.current;
  const paneWidth = chart?.timeScale().width() ?? 0;
  const compact = paneWidth > 0 && paneWidth < 560;
  // A long period (the reports page) would bury the curve under labels: past
  // a handful, an arrow is labelled while the pointer is on it.
  const crowded = fills.length > 6;
  const chips = layoutChips(
    fills.filter((f) => !crowded || (hoverTime !== null && Math.abs(f.time - hoverTime) <= 90)),
    {
      x: (t) => chart?.timeScale().timeToCoordinate(t as UTCTimestamp) ?? null,
      y: (v) => series?.priceToCoordinate(v) ?? null,
      width: paneWidth,
      height: chart ? height - chart.timeScale().height() : 0,
      compact,
    },
  );

  // The chart's container is always mounted. It used to be rendered only once
  // the data had arrived, but the chart is built when the component first
  // mounts — so on the deck, where the data comes a moment later, the chart
  // was never built and the box stayed empty.
  return (
    <div className="w-full">
      <div className="relative w-full overflow-hidden" style={{ height }}>
        <div ref={holder} className="h-full w-full" />
        <div className="pointer-events-none absolute inset-0 z-[3]" aria-hidden={!chips.length}>
          {chips.map((c) => (
            <span
              key={c.key}
              title={c.title}
              className={`absolute whitespace-nowrap rounded border bg-surface/95 px-2 text-[11px] font-semibold leading-[20px] tabular-nums shadow-sm ${TONE[c.tone]}`}
              style={{ left: c.left, top: c.top, height: CHIP_H }}
            >
              {c.text}
            </span>
          ))}
        </div>
        {!equity.length && (
          <div className="absolute inset-0 flex items-center justify-center rounded-md border border-dashed border-hairline text-xs text-ink-muted">
            No equity marks yet — the curve starts once an engine is running.
          </div>
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-muted" aria-label="Chart key">
        <li className="flex items-center gap-1.5">
          <span className="text-brand" aria-hidden>▲</span> BUY — when the option was bought
        </li>
        <li className="flex items-center gap-1.5">
          <span className="text-profit" aria-hidden>▼</span>
          <span className="-ml-1 text-loss" aria-hidden>▼</span> SELL — green made money, red lost
        </li>
        <li>Numbers give the order: 1 happened first.</li>
        {crowded && <li>Point at an arrow to read its order.</li>}
      </ul>
      {fills.length > 0 && (
        <ol className="sr-only">
          {fills.map((f) => (
            <li key={`${f.kind}-${f.at}`}>
              {f.kind === "buy" ? "Bought" : "Sold"} {f.contract} at {f.price?.toFixed(2)} at {f.clock}
              {f.net != null ? `, ${rupees(f.net)}` : ""}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
