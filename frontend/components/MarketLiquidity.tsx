"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Card, Empty } from "@/components/ui";
import { liquidityRows, liquiditySummary, spreadTone, type SpreadTone } from "@/lib/flow";
import type { ChainPayload } from "@/lib/types";

const H = 250;
const PAD_X = 8;
const TOP = 18;        // the band above the plot, for the NIFTY label
const LABEL_H = 20;    // the band below it, for strike labels

const TONE: Record<SpreadTone, { fill: string; label: string }> = {
  tight: { fill: "var(--status-good)", label: "tight (under 1%)" },
  fair: { fill: "var(--status-warning)", label: "fair (1–3%)" },
  wide: { fill: "var(--status-critical)", label: "wide (over 3%)" },
  none: { fill: "var(--text-muted)", label: "no quote" },
};

const count = (v: number) =>
  v >= 1e7 ? `${(v / 1e7).toFixed(2)}Cr` : v >= 1e5 ? `${(v / 1e5).toFixed(2)}L` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(Math.round(v));
const pct = (v: number | null) => (v == null ? "—" : `${v.toFixed(2)}%`);

/** The market's depth across the strikes: volume (calls up, puts down) over open interest, and how tight each quote is. */
export function MarketLiquidity({ chain, error }: { chain: ChainPayload | null; error: string | null }) {
  const [hover, setHover] = useState<number | null>(null);
  // Drawn at the width it is shown at, so labels stay readable on a phone instead of shrinking with it.
  const [W, setW] = useState(640);
  const observer = useRef<ResizeObserver | null>(null);
  // A callback ref: the chart's box appears only once the chain has loaded, after the first render.
  const box = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!el || typeof ResizeObserver === "undefined") return;
    setW(Math.max(280, Math.round(el.getBoundingClientRect().width)));
    observer.current = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    observer.current.observe(el);
  }, []);
  useEffect(() => () => observer.current?.disconnect(), []);
  const rows = useMemo(() => liquidityRows(chain?.rows), [chain]);
  const sum = useMemo(() => liquiditySummary(rows), [rows]);
  const lot = chain?.lot || 1;

  const geo = useMemo(() => {
    if (rows.length < 2) return null;
    const plotH = H - LABEL_H - TOP;
    const mid = TOP + plotH / 2;
    const slot = (W - 2 * PAD_X) / rows.length;
    const maxVol = Math.max(1, ...rows.flatMap((r) => [r.ceVol, r.peVol]));
    const maxOi = Math.max(1, ...rows.flatMap((r) => [r.ceOi, r.peOi]));
    const k0 = rows[0].strike;
    const k1 = rows[rows.length - 1].strike;
    const xOf = (k: number) => PAD_X + slot / 2 + ((k - k0) / (k1 - k0 || 1)) * (slot * (rows.length - 1));
    return { plotH, mid, slot, maxVol, maxOi, xOf, bottom: TOP + plotH };
  }, [rows, W]);

  const shown = hover != null ? rows[hover] : null;

  return (
    <Card
      title="Liquidity and volume"
      subtitle="Each strike's volume today, calls up and puts down, over its open interest (the faint towers). The cap shows how tight the bid–ask spread is"
    >
      {error && !chain ? (
        <Empty>Liquidity unavailable: {error}</Empty>
      ) : !chain ? (
        <Empty>Loading…</Empty>
      ) : !chain.available || !geo ? (
        <Empty>{chain.reason ?? "Liquidity is read from the option chain, which is not available right now."}</Empty>
      ) : (
        <div className="space-y-5">
          <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            <div className="min-w-0">
              <dt className="flex items-center gap-1.5 text-ink-muted"><span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm bg-call" /> Call volume</dt>
              <dd className="text-base font-semibold tabular-nums text-ink">{count(sum.ceVol / lot)} <span className="text-2xs font-normal text-ink-muted">lots</span></dd>
            </div>
            <div className="min-w-0">
              <dt className="flex items-center gap-1.5 text-ink-muted"><span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm bg-put" /> Put volume</dt>
              <dd className="text-base font-semibold tabular-nums text-ink">{count(sum.peVol / lot)} <span className="text-2xs font-normal text-ink-muted">lots</span></dd>
            </div>
            <div className="min-w-0">
              <dt className="text-ink-muted">Put / call, by volume</dt>
              <dd className="text-base font-semibold tabular-nums text-ink">{sum.pcrVolume == null ? "—" : sum.pcrVolume.toFixed(2)}</dd>
            </div>
            <div className="min-w-0">
              <dt className="text-ink-muted">Busiest contract</dt>
              <dd className="text-base font-semibold tabular-nums text-ink">{sum.busiest ? `${sum.busiest.strike} ${sum.busiest.side}` : "—"}</dd>
            </div>
          </dl>

          <div>
            <p className="mb-1 min-h-8 text-2xs tabular-nums text-ink-secondary" aria-live="polite">
              {shown
                ? `${shown.strike} · calls ${count(shown.ceVol / lot)} lots traded, ${count(shown.ceOi / lot)} open, spread ${pct(shown.ceSpread)} · puts ${count(shown.peVol / lot)} lots traded, ${count(shown.peOi / lot)} open, spread ${pct(shown.peSpread)}`
                : "Point at a strike for its figures."}
            </p>
            <div ref={box}>
            <svg
              viewBox={`0 0 ${W} ${H}`}
              width={W}
              height={H}
              className="block h-auto w-full"
              role="img"
              aria-label={`Volume and open interest across ${rows.length} strikes; busiest ${sum.busiest ? `${sum.busiest.strike} ${sum.busiest.side}` : "none"}`}
              onMouseLeave={() => setHover(null)}
            >
              <defs>
                <linearGradient id="liq-ce" x1="0" y1="1" x2="0" y2="0">
                  <stop offset="0%" stopColor="var(--series-call)" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="var(--series-call)" stopOpacity="1" />
                </linearGradient>
                <linearGradient id="liq-pe" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--series-put)" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="var(--series-put)" stopOpacity="1" />
                </linearGradient>
                <filter id="liq-glow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="2.5" result="b" />
                  <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
                </filter>
              </defs>

              {rows.map((r, i) => {
                const cx = geo.xOf(r.strike);
                const oiW = Math.max(4, geo.slot * 0.78);
                const vW = Math.max(3, geo.slot * 0.42);
                const half = geo.plotH / 2 - 12;   // room for the spread dot beyond the tallest bar
                const ceOiH = (r.ceOi / geo.maxOi) * half;
                const peOiH = (r.peOi / geo.maxOi) * half;
                const ceH = (r.ceVol / geo.maxVol) * half;
                const peH = (r.peVol / geo.maxVol) * half;
                const dim = hover != null && hover !== i;
                return (
                  <g key={r.strike} opacity={dim ? 0.35 : 1} onMouseEnter={() => setHover(i)}>
                    <rect x={cx - geo.slot / 2} y={0} width={geo.slot} height={H} fill="transparent" />
                    <rect x={cx - oiW / 2} y={geo.mid - 1 - ceOiH} width={oiW} height={ceOiH} rx={3} fill="var(--series-call)" opacity={0.13} />
                    <rect x={cx - oiW / 2} y={geo.mid + 1} width={oiW} height={peOiH} rx={3} fill="var(--series-put)" opacity={0.13} />
                    <rect x={cx - vW / 2} y={geo.mid - 1 - ceH} width={vW} height={ceH} rx={2} fill="url(#liq-ce)" />
                    <rect x={cx - vW / 2} y={geo.mid + 1} width={vW} height={peH} rx={2} fill="url(#liq-pe)" />
                    <circle cx={cx} cy={geo.mid - 1 - ceH - 5} r={3.2} fill={TONE[spreadTone(r.ceSpread)].fill} filter="url(#liq-glow)" />
                    <circle cx={cx} cy={geo.mid + 1 + peH + 5} r={3.2} fill={TONE[spreadTone(r.peSpread)].fill} filter="url(#liq-glow)" />
                    {(geo.slot >= 36 || i % (geo.slot >= 18 ? 2 : 3) === 0) && (
                      <text x={cx} y={H - 4} textAnchor="middle" fontSize="10" fill={r.strike === chain.atm ? "var(--brand)" : "var(--text-muted)"} fontWeight={r.strike === chain.atm ? 700 : 400}>
                        {r.strike}
                      </text>
                    )}
                  </g>
                );
              })}
              <line x1={PAD_X} x2={W - PAD_X} y1={geo.mid} y2={geo.mid} stroke="var(--hairline)" strokeWidth="1" />
              {chain.spot != null && chain.spot >= rows[0].strike && chain.spot <= rows[rows.length - 1].strike && (
                <g className="motion-safe:animate-pulse">
                  <line x1={geo.xOf(chain.spot)} x2={geo.xOf(chain.spot)} y1={TOP} y2={geo.bottom} stroke="var(--brand)" strokeWidth="1.5" strokeDasharray="4 3" />
                  <text x={geo.xOf(chain.spot)} y={12} textAnchor="middle" fontSize="10" fill="var(--brand)">NIFTY {chain.spot.toFixed(0)}</text>
                </g>
              )}
              {chain.analytics?.max_pain != null && chain.analytics.max_pain >= rows[0].strike && chain.analytics.max_pain <= rows[rows.length - 1].strike && (
                <text x={geo.xOf(chain.analytics.max_pain)} y={geo.bottom + 4} textAnchor="middle" fontSize="9" fill="var(--text-secondary)">▲</text>
              )}
            </svg>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-muted">
              <span className="flex items-center gap-1"><span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm bg-call" /> calls (up)</span>
              <span className="flex items-center gap-1"><span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm bg-put" /> puts (down)</span>
              <span>faint towers: open interest</span>
              {chain.analytics?.max_pain != null && <span>▲ max pain {chain.analytics.max_pain}</span>}
              {(["tight", "fair", "wide"] as const).map((t) => (
                <span key={t} className="flex items-center gap-1">
                  <span aria-hidden className="inline-block h-2 w-2 rounded-full" style={{ background: TONE[t].fill }} /> spread {TONE[t].label}
                </span>
              ))}
            </div>
          </div>

          <p className="text-2xs leading-relaxed text-ink-muted">
            Tightest quote: {sum.tightest ? `${sum.tightest.strike} ${sum.tightest.side} at ${pct(sum.tightest.spread)}` : "—"}. Widest:{" "}
            {sum.widest ? `${sum.widest.strike} ${sum.widest.side} at ${pct(sum.widest.spread)}` : "—"}. A wide spread costs that much of the premium on a market order, in and again out.
          </p>

          <details className="text-2xs">
            <summary className="cursor-pointer text-ink-secondary">The figures as a table</summary>
            <table className="mt-2 w-full tabular-nums">
              <thead>
                <tr className="border-b border-hairline text-ink-muted">
                  <th className="py-1 text-left font-medium">Strike</th>
                  <th className="py-1 text-right font-medium">Call lots</th>
                  <th className="py-1 text-right font-medium">Call spread</th>
                  <th className="py-1 text-right font-medium">Put lots</th>
                  <th className="py-1 text-right font-medium">Put spread</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.strike} className="border-b border-hairline/60 last:border-0">
                    <td className={`py-1 ${r.strike === chain.atm ? "font-semibold text-brand" : "text-ink"}`}>{r.strike}</td>
                    <td className="py-1 text-right">{count(r.ceVol / lot)}</td>
                    <td className="py-1 text-right">{pct(r.ceSpread)}</td>
                    <td className="py-1 text-right">{count(r.peVol / lot)}</td>
                    <td className="py-1 text-right">{pct(r.peSpread)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
      )}
    </Card>
  );
}
