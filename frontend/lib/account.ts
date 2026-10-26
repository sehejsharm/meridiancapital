import type { AngelMoney } from "@/lib/types";

/**
 * The desk's money words. Each has one meaning, on every page:
 *
 * - Angel balance: what Angel holds for the account now (the Angel app's
 *   trading balance). With a trade open, "account value" is that balance plus
 *   the open option at market.
 * - Trading P&L: what trades made or lost, after Angel's charges.
 * - Money in/out: anything else that moved the balance — a deposit, a
 *   withdrawal, a fee Angel took outside a trade.
 *
 * And one rule, which every figure built here keeps to the paisa:
 *
 *     Angel balance = opening balance + trading P&L + money in/out
 *
 * The desk used to show the program's own equity beside Angel's balance under
 * one word, and the two parted whenever Angel booked something outside a trade
 * (₹286.00 on 26 Oct 2026), with nothing on screen saying why.
 */
export interface AccountFigures {
  /** The figures are Angel's own (v8 programs); else the bot's equity, paper or older builds. */
  fromAngel: boolean;
  inTrade: boolean;
  /** Angel balance; with a trade open, the balance plus the open option at market. */
  value: number | null;
  /** Angel's opening ledger balance for the day. */
  opening: number | null;
  /** The day's trading P&L, after charges. */
  trading: number | null;
  /** Money in or out that was not a trade. */
  other: number | null;
  charges: number | null;
}

interface AccountLike {
  equity: number | null;
  day_pl: number | null;
  money?: AngelMoney | null;
}

const round2 = (n: number) => Math.round(n * 100) / 100;

export function accountFigures(account: AccountLike | null | undefined, inTrade = false): AccountFigures {
  if (!account) {
    return { fromAngel: false, inTrade, value: null, opening: null, trading: null, other: null, charges: null };
  }
  const m = account.money;
  if (!m || m.balance == null || m.opening_balance == null) {
    const opening = account.equity != null && account.day_pl != null ? round2(account.equity - account.day_pl) : null;
    return { fromAngel: false, inTrade, value: account.equity, opening, trading: account.day_pl, other: null, charges: null };
  }
  const opening = m.opening_balance;
  const charges = m.charges != null ? Math.abs(m.charges) : null;
  if (!inTrade && !m.charges_estimated) {
    // Flat, every charge settled: all three are Angel's figures and the rest is in/out.
    const trading = round2((m.realised_gross ?? 0) - (charges ?? 0));
    return {
      fromAngel: true, inTrade: false, value: m.balance, opening, trading,
      other: round2(m.balance - opening - trading), charges,
    };
  }
  // A trade open (or its charges not yet settled at Angel): the day's P&L counts it
  // at market, and money in/out is what Angel already held beyond its ledger at the open.
  const trading = m.day_net ?? account.day_pl;
  const other = m.added ?? 0;
  return {
    fromAngel: true, inTrade, value: trading != null ? round2(opening + other + trading) : account.equity,
    opening, trading, other, charges,
  };
}

/** The tile's name for `value`. */
export function valueLabel(f: AccountFigures): string {
  if (!f.fromAngel) return "Equity, the bot's figure";
  return f.inTrade ? "Account value" : "Angel balance";
}
