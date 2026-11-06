import type { ChainRow } from "@/lib/types";

/**
 * Drawing helpers for the option chain's OI bars and IV skew chart
 * (components/OptionChain.tsx). The figures themselves (PCR, max pain, OI
 * change) are worked out on the server: backend/app/market.py.
 */

/** A bar's length, 0–100 % of the heaviest open interest shown. */
export function oiPct(oi: number | null | undefined, max: number | null | undefined): number {
  if (oi == null || !max || max <= 0 || !Number.isFinite(oi)) return 0;
  return Math.max(0, Math.min(100, (oi / max) * 100));
}

/** "+12.3K", "−4.5L", "0": an OI change, signed, Indian units. */
export function signedCompact(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const body = a >= 1e7 ? `${(a / 1e7).toFixed(2)}Cr` : a >= 1e5 ? `${(a / 1e5).toFixed(2)}L` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : String(Math.round(a));
  return v > 0 ? `+${body}` : v < 0 ? `−${body}` : "0";
}

export interface SkewChart {
  ce: string;
  pe: string;
  lo: number;
  hi: number;
  /** x of a strike or the spot, in the chart's units. */
  x: (strike: number) => number;
}

/** SVG paths for calls' and puts' implied volatility across the strikes shown. */
export function skewChart(rows: ChainRow[], width: number, height: number, pad = 6): SkewChart | null {
  const pts = rows.filter((r) => r.ce?.iv != null || r.pe?.iv != null);
  if (pts.length < 2) return null;
  const ivs = pts.flatMap((r) => [r.ce?.iv, r.pe?.iv]).filter((v): v is number => v != null && Number.isFinite(v));
  let lo = Math.min(...ivs);
  let hi = Math.max(...ivs);
  if (hi - lo < 0.5) {
    lo -= 0.25;
    hi += 0.25;
  }
  const k0 = pts[0].strike;
  const k1 = pts[pts.length - 1].strike;
  const x = (k: number) => pad + ((k - k0) / (k1 - k0)) * (width - 2 * pad);
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (height - 2 * pad);
  const path = (side: "ce" | "pe") =>
    pts
      .filter((r) => r[side]?.iv != null)
      .map((r, i) => `${i ? "L" : "M"}${x(r.strike).toFixed(1)},${y(r[side]!.iv!).toFixed(1)}`)
      .join(" ");
  return { ce: path("ce"), pe: path("pe"), lo, hi, x };
}
