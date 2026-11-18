/**
 * Drawing helpers for the option-flow and liquidity panels (components/OptionFlow.tsx,
 * components/MarketLiquidity.tsx). The flow itself is worked out on the server
 * (backend/app/chain_flow.py); liquidity is read from the chain's own quotes.
 */
import type { ChainRow, ChainSide } from "./types.ts";

export type Reading = "long_buildup" | "short_buildup" | "short_covering" | "long_unwinding" | "quiet";

export interface FlowSide { volume: number | null; oi_change: number | null; price_change: number | null; turnover: number | null; reading: Reading }
export interface FlowStrike { strike: number; ce?: FlowSide; pe?: FlowSide }
export interface FlowPayload {
  available: boolean;
  reason?: string;
  since?: string;
  to?: string;
  window_from?: string;
  timeline?: { t: string; ce: number; pe: number }[];
  strikes?: FlowStrike[];
  totals?: { ce: number; pe: number; put_call_ratio: number | null };
}

/** What the reading means, in words: price and open interest read together. */
export const READING: Record<Reading, { short: string; long: string }> = {
  long_buildup: { short: "New buyers", long: "premium up, open interest up: new positions being bought" },
  short_buildup: { short: "New writers", long: "premium down, open interest up: new positions being sold" },
  short_covering: { short: "Writers exiting", long: "premium up, open interest down: sellers closing out" },
  long_unwinding: { short: "Buyers exiting", long: "premium down, open interest down: buyers closing out" },
  quiet: { short: "Quiet", long: "no clear change in premium and open interest" },
};

/** ₹1.23Cr, ₹4.5L, ₹12.3K: a rupee amount in Indian units. */
export function rupeesCompact(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const body = a >= 1e7 ? `${(a / 1e7).toFixed(2)}Cr` : a >= 1e5 ? `${(a / 1e5).toFixed(2)}L` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : a.toFixed(0);
  return `${v < 0 ? "−" : ""}₹${body}`;
}

/** The bid–ask spread as a share of the mid price, in percent; null without both quotes. */
export function spreadPct(side: ChainSide | null | undefined): number | null {
  const bid = side?.bid;
  const ask = side?.ask;
  if (bid == null || ask == null || bid <= 0 || ask <= 0 || ask < bid) return null;
  return ((ask - bid) / ((ask + bid) / 2)) * 100;
}

export type SpreadTone = "tight" | "fair" | "wide" | "none";

/** Tight under 1%, fair under 3%, wide beyond: what a market order would lose to the spread. */
export function spreadTone(pct: number | null): SpreadTone {
  if (pct == null) return "none";
  return pct < 1 ? "tight" : pct < 3 ? "fair" : "wide";
}

export interface LiquidityRow {
  strike: number;
  ceVol: number;
  peVol: number;
  ceOi: number;
  peOi: number;
  ceSpread: number | null;
  peSpread: number | null;
}

export function liquidityRows(rows: ChainRow[] | undefined): LiquidityRow[] {
  return (rows ?? []).map((r) => ({
    strike: r.strike,
    ceVol: r.ce?.volume ?? 0,
    peVol: r.pe?.volume ?? 0,
    ceOi: r.ce?.oi ?? 0,
    peOi: r.pe?.oi ?? 0,
    ceSpread: spreadPct(r.ce),
    peSpread: spreadPct(r.pe),
  }));
}

export interface LiquiditySummary {
  ceVol: number;
  peVol: number;
  pcrVolume: number | null;
  busiest: { strike: number; side: "CE" | "PE"; volume: number } | null;
  tightest: { strike: number; side: "CE" | "PE"; spread: number } | null;
  widest: { strike: number; side: "CE" | "PE"; spread: number } | null;
}

export function liquiditySummary(rows: LiquidityRow[]): LiquiditySummary {
  const sides = rows.flatMap((r) => [
    { strike: r.strike, side: "CE" as const, volume: r.ceVol, spread: r.ceSpread },
    { strike: r.strike, side: "PE" as const, volume: r.peVol, spread: r.peSpread },
  ]);
  const ceVol = rows.reduce((s, r) => s + r.ceVol, 0);
  const peVol = rows.reduce((s, r) => s + r.peVol, 0);
  const busiest = sides.reduce<(typeof sides)[number] | null>((b, x) => (x.volume > (b?.volume ?? 0) ? x : b), null);
  const priced = sides.filter((x) => x.spread != null) as { strike: number; side: "CE" | "PE"; volume: number; spread: number }[];
  const tightest = priced.reduce<(typeof priced)[number] | null>((b, x) => (b == null || x.spread < b.spread ? x : b), null);
  const widest = priced.reduce<(typeof priced)[number] | null>((b, x) => (b == null || x.spread > b.spread ? x : b), null);
  return {
    ceVol,
    peVol,
    pcrVolume: ceVol > 0 ? peVol / ceVol : null,
    busiest: busiest ? { strike: busiest.strike, side: busiest.side, volume: busiest.volume } : null,
    tightest: tightest ? { strike: tightest.strike, side: tightest.side, spread: tightest.spread } : null,
    widest: widest ? { strike: widest.strike, side: widest.side, spread: widest.spread } : null,
  };
}

/** Mirrored bars, one per minute: calls above the middle line, puts below, on one rupee scale. */
export function flowBars(timeline: { t: string; ce: number; pe: number }[], width: number, height: number, gap = 1) {
  const n = timeline.length;
  const max = Math.max(1, ...timeline.map((x) => Math.max(x.ce, x.pe)));
  const mid = height / 2;
  const slot = n ? width / n : width;
  const w = Math.max(1, slot - gap);
  return {
    max,
    mid,
    bars: timeline.map((x, i) => ({
      t: x.t,
      x: i * slot,
      w,
      ceH: (x.ce / max) * (mid - 2),
      peH: (x.pe / max) * (mid - 2),
      ce: x.ce,
      pe: x.pe,
    })),
  };
}

/** The share of the heaviest value, 0–100, for a bar's length. */
export function share(v: number | null | undefined, max: number): number {
  if (v == null || !Number.isFinite(v) || max <= 0) return 0;
  return Math.max(0, Math.min(100, (Math.abs(v) / max) * 100));
}
