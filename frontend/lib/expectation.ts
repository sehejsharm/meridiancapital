/**
 * Live results against what the backtest led us to expect.
 *
 * The expectation is two numbers for one trade of one lot: the average result
 * and how much a single trade typically swings (its standard deviation). After
 * n trades the expected total is the sum of the averages, and luck alone moves
 * the total about two standard deviations of the sum either way. A live line
 * that stays inside that band is behaving like the backtest; one that leaves
 * it below is not, and that is worth stopping to look at.
 */

export interface Expectation { mean: number; sd: number; since: string; label?: string; set_by?: string; set_at?: string }
export interface ExpectationTrade { day: string; net: number; lots: number }
export interface ExpectationView { expectation: Expectation | null; trades: ExpectationTrade[] }

export interface BandPoint { n: number; day: string; actual: number; expected: number; low: number; high: number }

export type Verdict = "none" | "early" | "inside" | "below" | "above";

export const MIN_TRADES_FOR_VERDICT = 10;

/** One point after each trade: the running real total, the expected total and the two-sigma band around it. */
export function band(exp: Pick<Expectation, "mean" | "sd">, trades: ExpectationTrade[]): BandPoint[] {
  let actual = 0;
  let expected = 0;
  let variance = 0;
  return trades.map((t, i) => {
    const lots = Math.max(1, t.lots || 1);
    actual += t.net;
    expected += lots * exp.mean;
    variance += (lots * exp.sd) ** 2;
    const two = 2 * Math.sqrt(variance);
    return { n: i + 1, day: t.day, actual: round2(actual), expected: round2(expected), low: round2(expected - two), high: round2(expected + two) };
  });
}

export function verdict(points: BandPoint[]): Verdict {
  const last = points[points.length - 1];
  if (!last) return "none";
  if (last.n < MIN_TRADES_FOR_VERDICT) return "early";
  if (last.actual < last.low) return "below";
  return last.actual > last.high ? "above" : "inside";
}

export function verdictText(v: Verdict, n: number): string {
  switch (v) {
    case "none": return "No real-money trade since the start date yet.";
    case "early": return `${n} trade${n === 1 ? "" : "s"} so far: too few to judge. The band means something from ${MIN_TRADES_FOR_VERDICT} trades.`;
    case "inside": return "Inside the band: the live results are what the backtest allows for, good luck or bad.";
    case "below": return "Below the band: worse than bad luck alone explains. The backtest is not describing live trading; stop and look before adding size.";
    case "above": return "Above the band: better than the backtest expects. Pleasant, and no more a proof than a bad run is.";
  }
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}
