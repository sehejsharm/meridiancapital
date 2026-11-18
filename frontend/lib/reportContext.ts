/**
 * Reports covers the dates chosen; the Blotter covers every real-money trade.
 * These lines say how the two fit, so their figures never look like they disagree.
 */
import { dayLabel, signedMoney } from "./format.ts";

export interface ContextPart { trades: number; net: number; wins: number; win_rate_pct: number | null; first: string | null; last: string | null }
export interface ReportContext { before: ContextPart; period: ContextPart; after: ContextPart; all: ContextPart }

function part(label: string, p: ContextPart): string {
  const n = `${p.trades} trade${p.trades === 1 ? "" : "s"}`;
  return `${label}: ${n}, ${signedMoney(p.net)}${p.trades ? `, ${p.wins} won (${(p.win_rate_pct ?? 0).toFixed(1)}%)` : ""}`;
}

/** The rows of the "how this adds up" box: before + this period (+ after) = all real-money trades. */
export function contextRows(c: ReportContext, start: string, end: string): { label: string; text: string; strong?: boolean }[] {
  const rows: { label: string; text: string; strong?: boolean }[] = [];
  if (c.before.trades) rows.push({ label: "before", text: part(`Before ${dayLabel(start)}`, c.before) });
  rows.push({ label: "period", text: part(`This report, ${dayLabel(start)} to ${dayLabel(end)}`, c.period) });
  if (c.after.trades) rows.push({ label: "after", text: part(`After ${dayLabel(end)}`, c.after) });
  rows.push({ label: "all", text: part("All real-money trades, as on the Blotter", c.all), strong: true });
  return rows;
}

/** One sentence for when the period does not cover every trade. Null when it does. */
export function contextNote(c: ReportContext): string | null {
  if (c.before.trades === 0 && c.after.trades === 0) return null;
  const w = (p: ContextPart) => `${(p.win_rate_pct ?? 0).toFixed(1)}%`;
  return `This report counts ${c.period.trades} of your ${c.all.trades} real-money trades, so its P&L (${signedMoney(c.period.net)}) and win rate (${w(c.period)}) differ from the Blotter's (${signedMoney(c.all.net)}, ${w(c.all)}), which counts all of them. Choose dates from ${c.all.first ? dayLabel(c.all.first) : "the first trade"} to today to match it.`;
}

/** The Blotter's line saying what its headline covers. */
export function blotterScope(n: number, first: string | null): string {
  if (!n) return "No real-money trade yet.";
  return `These figures count all ${n} real-money trade${n === 1 ? "" : "s"}${first ? ` since ${dayLabel(first)}` : ""}. Reports counts only the dates you choose (the last month unless you change them), so its figures differ; it shows how the two add up.`;
}
