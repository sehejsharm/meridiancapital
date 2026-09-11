"use client";

import { useMemo, useRef, useState } from "react";

import { istTime, money, signedMoney } from "@/lib/format";

export interface ChartPoint {
  label: string;
  value: number;
  secondary?: number | null;
}

const VB_W = 1000;
const VB_H = 300;
const HEIGHT = 240;
const GUTTER = 76; // right-hand room for the value axis, in CSS pixels

/**
 * Single-series equity curve.
 *
 * The plot is drawn in SVG with `preserveAspectRatio="none"` so it fills its box
 * at any width instead of letterboxing on a phone; strokes stay true via
 * non-scaling-stroke. Everything textual — axis labels, markers, tooltip — lives
 * in an HTML layer above it, so type is real CSS pixels rather than shrinking
 * with the viewBox. An equivalent table is always present for screen readers.
 */
export function EquityChart({
  points,
  baseline,
  emptyMessage = "No equity samples yet.",
  xLabel = (p: ChartPoint) => istTime(p.label),
}: {
  points: ChartPoint[];
  baseline?: number | null;
  emptyMessage?: string;
  xLabel?: (p: ChartPoint) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const plotRef = useRef<HTMLDivElement>(null);

  const geo = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map((p) => p.value);
    const candidates = baseline != null ? [...values, baseline] : values;
    const rawMin = Math.min(...candidates);
    const rawMax = Math.max(...candidates);
    const span = rawMax - rawMin || Math.max(1, Math.abs(rawMax) * 0.01);
    const min = rawMin - span * 0.14;
    const max = rawMax + span * 0.14;

    /** Fractions of the plot box, 0..1 — the shared language of both layers. */
    const fx = (i: number) => i / (points.length - 1);
    const fy = (v: number) => 1 - (v - min) / (max - min);

    const vx = (i: number) => fx(i) * VB_W;
    const vy = (v: number) => fy(v) * VB_H;

    const line = points.map((p, i) => `${i === 0 ? "M" : "L"} ${vx(i)} ${vy(p.value)}`).join(" ");
    const area = `${line} L ${VB_W} ${VB_H} L 0 ${VB_H} Z`;
    const ticks = Array.from({ length: 4 }, (_, i) => min + ((max - min) * (i + 1)) / 5);

    return { fx, fy, vy, line, area, ticks };
  }, [points, baseline]);

  if (!geo) {
    return (
      <div
        className="flex items-center justify-center text-xs text-ink-muted"
        style={{ height: HEIGHT }}
      >
        {emptyMessage}
      </div>
    );
  }

  const { fx, fy, vy, line, area, ticks } = geo;
  const last = points[points.length - 1];
  const first = points[0];
  const change = last.value - (baseline ?? first.value);
  const active = hover != null ? points[hover] : null;

  const onMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = plotRef.current?.getBoundingClientRect();
    if (!box) return;
    const ratio = (event.clientX - box.left) / box.width;
    const index = Math.round(ratio * (points.length - 1));
    setHover(Math.min(points.length - 1, Math.max(0, index)));
  };

  const pct = (n: number) => `${n * 100}%`;

  return (
    <div>
      <div className="relative" style={{ paddingRight: GUTTER }}>
        <div
          ref={plotRef}
          className="relative touch-pan-y"
          style={{ height: HEIGHT }}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          <svg
            viewBox={`0 0 ${VB_W} ${VB_H}`}
            preserveAspectRatio="none"
            className="absolute inset-0 h-full w-full"
            aria-hidden="true"
          >
            <defs>
              <linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--series-1)" stopOpacity="0.2" />
                <stop offset="100%" stopColor="var(--series-1)" stopOpacity="0" />
              </linearGradient>
            </defs>

            {ticks.map((value) => (
              <line
                key={value}
                x1={0}
                y1={vy(value)}
                x2={VB_W}
                y2={vy(value)}
                stroke="var(--gridline)"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
            ))}

            {baseline != null && (
              <line
                x1={0}
                y1={vy(baseline)}
                x2={VB_W}
                y2={vy(baseline)}
                stroke="var(--baseline)"
                strokeWidth="1"
                strokeDasharray="5 5"
                vectorEffect="non-scaling-stroke"
              />
            )}

            <path d={area} fill="url(#equity-fill)" />
            <path
              d={line}
              fill="none"
              stroke="var(--series-1)"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />

            {hover != null && (
              <line
                x1={fx(hover) * VB_W}
                y1={0}
                x2={fx(hover) * VB_W}
                y2={VB_H}
                stroke="var(--text-muted)"
                strokeWidth="1"
                strokeDasharray="3 3"
                vectorEffect="non-scaling-stroke"
              />
            )}
          </svg>

          {/* Markers as HTML so they stay circular under the stretched viewBox. */}
          <Dot left={fx(points.length - 1)} top={fy(last.value)} />
          {active && hover != null && <Dot left={fx(hover)} top={fy(active.value)} large />}

          {baseline != null && (
            <span
              className="pointer-events-none absolute left-0 -translate-y-full pb-0.5 text-[9px] tracking-[0.1em] text-ink-muted"
              style={{ top: pct(fy(baseline)) }}
            >
              OPEN
            </span>
          )}

          {active && hover != null && (
            <div
              className="pointer-events-none absolute top-2 z-10 w-max rounded-md border border-hairline bg-surface-raised px-2.5 py-1.5 shadow-lg"
              style={{
                left: pct(fx(hover)),
                transform: fx(hover) > 0.55 ? "translateX(-105%)" : "translateX(10px)",
              }}
            >
              <div className="text-2xs uppercase tracking-[0.12em] text-ink-muted">
                {xLabel(active)}
              </div>
              <div className="text-sm font-semibold tabular-nums text-ink">
                {money(active.value)}
              </div>
              {active.secondary != null && (
                <div className="text-2xs tabular-nums text-ink-secondary">
                  Day P&amp;L {signedMoney(active.secondary)}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Value axis, in the reserved gutter. */}
        <div className="pointer-events-none absolute inset-y-0 right-0" style={{ width: GUTTER }}>
          {ticks.map((value) => (
            <span
              key={value}
              className="absolute left-2 -translate-y-1/2 whitespace-nowrap text-[10px] tabular-nums text-ink-muted"
              style={{ top: pct(fy(value)) }}
            >
              {money(value)}
            </span>
          ))}
        </div>
      </div>

      <div className="flex justify-between pt-1 text-[10px] tabular-nums text-ink-muted"
        style={{ paddingRight: GUTTER }}
      >
        <span>{xLabel(first)}</span>
        <span>{xLabel(last)}</span>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-2xs text-ink-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full bg-series" />
          Account equity, from Angel One
        </span>
        <span className="tabular-nums">
          {points.length} samples · {signedMoney(change)} over range
        </span>
      </div>

      {/* sr-only must sit on a block wrapper: a table treats its own height as a
          minimum, so clipping it directly leaves thousands of pixels of dead
          scroll on the page. */}
      <div className="sr-only">
        <table>
          <caption>Account equity over the selected range</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Equity</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p, i) => (
              <tr key={`${p.label}-${i}`}>
                <td>{xLabel(p)}</td>
                <td>{money(p.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Dot({ left, top, large = false }: { left: number; top: number; large?: boolean }) {
  return (
    <span
      className={`pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full bg-series ring-2 ring-[color:var(--surface-1)] ${
        large ? "h-2.5 w-2.5" : "h-2 w-2"
      }`}
      style={{ left: `${left * 100}%`, top: `${top * 100}%` }}
    />
  );
}
