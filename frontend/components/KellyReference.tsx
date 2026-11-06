"use client";

import { useEffect, useState } from "react";

import { closedTrades, kelly, type KellyView } from "@/lib/analytics";
import { apiGet } from "@/lib/client-api";
import { money, percent } from "@/lib/format";
import type { TradeRow } from "@/lib/types";

/**
 * Kelly from the last 30 real-money trades, beside the Sizing settings. A
 * reference only: nothing here changes a parameter.
 */
export function KellyReference() {
  const [k, setK] = useState<KellyView | null | undefined>(undefined);
  useEffect(() => {
    apiGet<{ trades: TradeRow[] }>("/trades?limit=200")
      .then((d) => setK(kelly(closedTrades(d.trades ?? []), 30)))
      .catch(() => setK(null));
  }, []);
  if (k === undefined) return null;
  return (
    <div className="rounded-md border border-hairline bg-surface-raised px-3 py-2.5 text-2xs leading-relaxed text-ink-secondary">
      <span className="font-semibold uppercase tracking-[0.12em] text-brand">Sizing reference · Kelly</span>{" "}
      {k ? (
        <>
          Last {k.n} real-money trades: win rate {percent(k.winRate, 0)}, average win {money(k.avgWin)} against average
          loss {money(k.avgLoss)} (payoff {k.payoff.toFixed(2)}).{" "}
          {k.kelly > 0
            ? `Kelly says risk ${percent(k.kelly, 1)} of capital per trade; half-Kelly ${percent(k.half, 1)}.`
            : "Kelly finds no edge in this sample (0%)."}{" "}
          Risk per trade here is deploy × hard stop. Shown for reference only — nothing is applied.
        </>
      ) : (
        "Needs at least 10 real-money trades with both wins and losses."
      )}
    </div>
  );
}
