/** The Deck's metrics panel: types and the few readings worked out in the browser. */

export interface Performance {
  trades: number;
  net?: number;
  win_rate?: number;
  profit_factor?: number | null;
  expectancy?: number;
  avg_win?: number | null;
  avg_loss?: number | null;
  sharpe_per_trade?: number | null;
  sortino_per_trade?: number | null;
  max_drawdown?: number;
  from_best?: number;
  streak?: { kind: "win" | "loss" | null; count: number };
}

export interface VsNifty {
  available: boolean;
  reason?: string;
  days?: number;
  since?: string;
  enough?: boolean;
  alpha_annual?: number;
  alpha_daily?: number;
  beta?: number | null;
  correlation?: number | null;
  strategy_return?: number;
  nifty_return?: number;
  excess?: number;
}

export interface MarketGauges {
  nifty_close: number | null;
  nifty_change_pct: number | null;
  nifty_as_of: string | null;
  vix: number | null;
  pcr_oi: number | null;
  atm_iv: number | null;
  max_pain: number | null;
  call_wall: number | null;
  put_wall: number | null;
}

export interface DeskMetrics { performance: Performance; vs_nifty: VsNifty; market: MarketGauges }

/** Where NIFTY sits in its 90-minute channel, 0 (at the low) to 100 (at the high); outside it, beyond. */
export function channelPosition(spot: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined): number | null {
  if (spot == null || lo == null || hi == null || hi <= lo) return null;
  return Math.round(((spot - lo) / (hi - lo)) * 100);
}

/** The market's direction in words, from the channel. */
export function trendWord(pos: number | null): string {
  if (pos == null) return "—";
  if (pos > 100) return "Broke out up";
  if (pos < 0) return "Broke out down";
  if (pos >= 75) return "Near the high";
  if (pos <= 25) return "Near the low";
  return "Mid-channel";
}

/** Beta in words: how much the results move with NIFTY. */
export function betaWord(beta: number | null | undefined): string {
  if (beta == null) return "—";
  const a = Math.abs(beta);
  if (a < 0.2) return "barely moves with NIFTY";
  if (a < 0.6) return beta > 0 ? "moves partly with NIFTY" : "moves partly against NIFTY";
  return beta > 0 ? "moves with NIFTY" : "moves against NIFTY";
}

/** The open position's trail: the stop now, what it locks in, and how far the price is from it. */
export function trail(price: number | null | undefined, stop: number | null | undefined, entry: number | null | undefined) {
  if (price == null || stop == null || entry == null || entry <= 0) return null;
  return {
    stop,
    locked: stop / entry - 1,
    room: price > 0 ? (price - stop) / price : null,
  };
}
