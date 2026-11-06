import type { TradeRow, TradeStats } from "@/lib/types";

/** A trade's category: the server's when it sent one, else worked out the same way. */
export function matchOf(t: TradeRow): "matched" | "fund_transfer" | "provisional" {
  if (t.match) return t.match;
  if (t.ledger_gap != null && Math.abs(t.ledger_gap) >= 0.01) return "fund_transfer";
  if (t.reconciled === "Angel ledger" || t.reconciled === "Angel balance") return "matched";
  return "provisional";
}

/**
 * The header's reconciliation line, one count per category, summing to the
 * trades it covers. It used to read "26 of 27 matched to Angel" over a table
 * with 25 ticks and two "moved outside trading" notes.
 */
export function matchLine(stats: TradeStats): string {
  const parts = [`${stats.reconciled ?? 0} of ${stats.n} matched to Angel`];
  if (stats.fund_transfer) parts.push(`${stats.fund_transfer} on fund-transfer days`);
  if (stats.provisional) parts.push(`${stats.provisional} provisional`);
  return parts.join(" · ");
}
