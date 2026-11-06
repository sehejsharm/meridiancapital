import type { TradeRow } from "@/lib/types";

/**
 * Performance analytics over the real-money blotter. Pure functions over the
 * trades the desk already has (entry/exit times, Angel's net), so every figure
 * here can be checked against the blotter by hand.
 */

export interface ClosedTrade {
  day: string;        // session date, YYYY-MM-DD
  entry: string;      // entry timestamp (naive IST)
  hour: number;       // entry hour, IST
  weekday: number;    // 0 = Monday … 4 = Friday
  net: number;        // after charges, Angel's figure where known
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Real-money closed trades, oldest first, with Angel's net where reconciled. */
export function closedTrades(trades: TradeRow[], mode: "live" | "paper" = "live"): ClosedTrade[] {
  return trades
    .filter((t) => t.mode === mode && t.exit_ts)
    .map((t) => {
      const entry = (t.entry_ts ?? "").replace(" ", "T");
      const day = t.session_date;
      const d = new Date(`${day}T00:00:00Z`);
      const net = Number(t.net_angel ?? t.net ?? 0);
      return {
        day,
        entry,
        hour: Number(entry.slice(11, 13)) || 0,
        weekday: (d.getUTCDay() + 6) % 7,
        net: Number.isFinite(net) ? net : 0,
      };
    })
    .sort((a, b) => a.entry.localeCompare(b.entry));
}

/** Net per session date. */
export function dailyPnl(trades: ClosedTrade[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const t of trades) out.set(t.day, round2((out.get(t.day) ?? 0) + t.net));
  return out;
}

export interface CurvePoint {
  i: number;
  day: string;
  cum: number;
  peak: number;
  drawdown: number; // ≤ 0, rupees below the running peak
}

/** Cumulative trading P&L trade by trade, with the running peak and the drawdown under it. */
export function underwater(trades: ClosedTrade[]): CurvePoint[] {
  let cum = 0;
  let peak = 0;
  return trades.map((t, i) => {
    cum = round2(cum + t.net);
    peak = Math.max(peak, cum);
    return { i, day: t.day, cum, peak, drawdown: round2(cum - peak) };
  });
}

/** Win rate over the last `n` trades at each point (null until `n` trades exist). */
export function rollingWinRate(trades: ClosedTrade[], n = 10): (number | null)[] {
  return trades.map((_, i) => {
    if (i + 1 < n) return null;
    const w = trades.slice(i + 1 - n, i + 1);
    return w.filter((t) => t.net > 0).length / n;
  });
}

/** Per-trade Sortino over the last `n` trades: mean / downside deviation (null until `n` trades). */
export function rollingSortino(trades: ClosedTrade[], n = 20): (number | null)[] {
  return trades.map((_, i) => {
    if (i + 1 < n) return null;
    const w = trades.slice(i + 1 - n, i + 1).map((t) => t.net);
    const mean = w.reduce((a, b) => a + b, 0) / n;
    const down = Math.sqrt(w.reduce((a, b) => a + Math.min(b, 0) ** 2, 0) / n);
    return down > 0 ? round2(mean / down) : null;
  });
}

export interface Bucket {
  key: string;
  n: number;
  wins: number;
  net: number;
  avg: number;
  winRate: number;
}

function bucketise(trades: ClosedTrade[], keyOf: (t: ClosedTrade) => string, order: string[]): Bucket[] {
  const map = new Map<string, ClosedTrade[]>();
  for (const t of trades) {
    const k = keyOf(t);
    map.set(k, [...(map.get(k) ?? []), t]);
  }
  return order
    .filter((k) => map.has(k))
    .map((k) => {
      const ts = map.get(k)!;
      const net = round2(ts.reduce((a, t) => a + t.net, 0));
      const wins = ts.filter((t) => t.net > 0).length;
      return { key: k, n: ts.length, wins, net, avg: round2(net / ts.length), winRate: wins / ts.length };
    });
}

/** Results by the hour the trade was entered (IST). */
export function byEntryHour(trades: ClosedTrade[]): Bucket[] {
  const order = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}:00`);
  return bucketise(trades, (t) => `${String(t.hour).padStart(2, "0")}:00`, order);
}

/** Results by weekday. */
export function byWeekday(trades: ClosedTrade[]): Bucket[] {
  return bucketise(trades, (t) => WEEKDAYS[t.weekday], WEEKDAYS);
}

export interface HeatCell {
  day: string;
  net: number | null; // null: no trade that day
  weekday: number;
}

/** Calendar weeks (Monday first) from the first to the last trading day, for a contributions-style grid. */
export function calendarWeeks(daily: Map<string, number>): HeatCell[][] {
  const days = [...daily.keys()].sort();
  if (!days.length) return [];
  const start = new Date(`${days[0]}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
  const end = new Date(`${days[days.length - 1]}T00:00:00Z`);
  const weeks: HeatCell[][] = [];
  for (const d = new Date(start); d <= end; ) {
    const week: HeatCell[] = [];
    for (let wd = 0; wd < 5; wd++) {
      const iso = new Date(d.getTime() + wd * 86_400_000).toISOString().slice(0, 10);
      week.push({ day: iso, weekday: wd, net: daily.has(iso) ? daily.get(iso)! : null });
    }
    weeks.push(week);
    d.setUTCDate(d.getUTCDate() + 7);
  }
  return weeks;
}

/** Deterministic PRNG (mulberry32), so a simulation reads the same on every reload. */
export function prng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface MonteCarlo {
  paths: number;
  horizon: number;
  pDailyKill: number;
  pWeeklyKill: number;
  pDrawdown: number;
  pLoss: number;
  final: { p5: number; p50: number; p95: number };
  worstDrawdown: { p50: number; p95: number };
}

/**
 * Resample the historical trades (one a day, as the strategy trades) over the
 * next `horizon` sessions, `paths` times. Reports how often the daily kill
 * (one session's loss at or past `dailyKill`), the weekly kill (five sessions'
 * loss at or past `weeklyKill`) or a drawdown of `ddFrac` of `capital` would
 * have been hit. It assumes the future looks like the sample: a guide, not a forecast.
 */
export function monteCarlo(
  nets: number[],
  opts: { horizon?: number; paths?: number; dailyKill: number; weeklyKill: number; capital: number; ddFrac?: number; seed?: number },
): MonteCarlo | null {
  if (nets.length < 5) return null;
  const horizon = opts.horizon ?? 60;
  const paths = opts.paths ?? 2000;
  const ddFrac = opts.ddFrac ?? 0.45;
  const rnd = prng(opts.seed ?? 7);
  let daily = 0, weekly = 0, dd = 0, loss = 0;
  const finals: number[] = [];
  const dds: number[] = [];
  for (let p = 0; p < paths; p++) {
    let cum = 0, peak = 0, worst = 0, week = 0;
    let hitDaily = false, hitWeekly = false;
    for (let i = 0; i < horizon; i++) {
      const x = nets[Math.floor(rnd() * nets.length)];
      if (x <= -Math.abs(opts.dailyKill)) hitDaily = true;
      if (i % 5 === 0) week = 0;
      week += x;
      if (week <= -Math.abs(opts.weeklyKill)) hitWeekly = true;
      cum += x;
      peak = Math.max(peak, cum);
      worst = Math.min(worst, cum - peak);
    }
    if (hitDaily) daily++;
    if (hitWeekly) weekly++;
    if (-worst >= ddFrac * opts.capital) dd++;
    if (cum < 0) loss++;
    finals.push(cum);
    dds.push(-worst);
  }
  finals.sort((a, b) => a - b);
  dds.sort((a, b) => a - b);
  const q = (xs: number[], f: number) => round2(xs[Math.min(xs.length - 1, Math.floor(f * xs.length))]);
  return {
    paths,
    horizon,
    pDailyKill: daily / paths,
    pWeeklyKill: weekly / paths,
    pDrawdown: dd / paths,
    pLoss: loss / paths,
    final: { p5: q(finals, 0.05), p50: q(finals, 0.5), p95: q(finals, 0.95) },
    worstDrawdown: { p50: q(dds, 0.5), p95: q(dds, 0.95) },
  };
}

export interface KellyView {
  n: number;
  winRate: number;
  avgWin: number;
  avgLoss: number;
  payoff: number;   // avg win / avg loss
  kelly: number;    // fraction of capital at risk, can be ≤ 0
  half: number;
}

/**
 * Kelly over the last `n` trades: f* = p − (1 − p) / b, b = avg win / avg loss.
 * A reference for the risk taken per trade (the loss at the stop), never applied
 * automatically; half-Kelly is the usual practical ceiling.
 */
export function kelly(trades: ClosedTrade[], n = 30): KellyView | null {
  const w = trades.slice(-n);
  const wins = w.filter((t) => t.net > 0).map((t) => t.net);
  const losses = w.filter((t) => t.net <= 0).map((t) => -t.net);
  if (w.length < 10 || !wins.length || !losses.length) return null;
  const p = wins.length / w.length;
  const avgWin = wins.reduce((a, b) => a + b, 0) / wins.length;
  const avgLoss = losses.reduce((a, b) => a + b, 0) / losses.length;
  const b = avgWin / avgLoss;
  const k = p - (1 - p) / b;
  return { n: w.length, winRate: p, avgWin: round2(avgWin), avgLoss: round2(avgLoss), payoff: round2(b), kelly: k, half: k / 2 };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100 + 0;
}
