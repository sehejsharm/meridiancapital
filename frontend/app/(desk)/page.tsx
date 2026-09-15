"use client";

import { useCallback, useEffect, useState } from "react";

import { AlgoCard } from "@/components/AlgoCard";
import { HealthStrip } from "@/components/HealthStrip";
import { LiveTape } from "@/components/LiveTape";
import { MarkedChart } from "@/components/MarkedChart";
import { NewsPanel } from "@/components/NewsPanel";
import { NiftyTicker } from "@/components/NiftyTicker";
import { TradingViewChart } from "@/components/TradingViewChart";
import { Badge, Card, Empty, StatTile } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { money, signedMoney } from "@/lib/format";
import { useLiveFeed } from "@/lib/LiveContext";
import type { AlgoList, EquityPoint, TradeRow } from "@/lib/types";

/**
 * The deck.
 *
 * Layout follows the fleet rather than a fixed shape: one algorithm gets a wide
 * card with room for its figures, several get a denser grid. A desk running six
 * strategies and a desk running one should not be read the same way.
 */
export default function DeckPage() {
  const { snapshot, status, events, connection } = useLiveFeed();
  const [algos, setAlgos] = useState<AlgoList | null>(null);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadAlgos = useCallback(async () => {
    try {
      setAlgos(await apiGet<AlgoList>("/algos"));
    } catch {
      /* the fleet summary in status still renders */
    }
  }, []);

  useEffect(() => {
    void loadAlgos();
    const t = setInterval(() => void loadAlgos(), 10_000);
    return () => clearInterval(t);
  }, [loadAlgos]);

  useEffect(() => {
    void (async () => {
      try {
        const [e, t] = await Promise.all([
          apiGet<{ curve: EquityPoint[] }>("/equity?limit=600"),
          apiGet<{ trades: TradeRow[] }>("/trades?limit=80"),
        ]);
        setEquity(e.curve ?? []);
        setTrades(t.trades ?? []);
      } catch {
        /* chart falls back to its empty state */
      }
    })();
  }, [snapshot?.ts]);

  const control = useCallback(
    async (algoId: string, action: "start" | "stop") => {
      setBusy(algoId);
      setNotice(null);
      try {
        await apiPost(`/algos/${algoId}/${action}`);
        await loadAlgos();
      } catch (e) {
        setNotice(e instanceof Error ? e.message : `could not ${action} ${algoId}`);
      } finally {
        setBusy(null);
      }
    },
    [loadAlgos],
  );

  const fleet = status?.fleet;
  const list = algos?.algos ?? [];
  const single = list.length <= 1;
  const account = snapshot?.account;

  return (
    <div className="space-y-5">
      <NiftyTicker />

      {notice && (
        <div role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">
          {notice}
        </div>
      )}

      {fleet && fleet.live_running > 0 && (
        <div className="rounded-lg border border-critical/50 bg-critical/10 px-4 py-2.5 text-xs text-critical">
          <strong className="font-semibold">
            {fleet.live_running} algorithm{fleet.live_running === 1 ? "" : "s"} trading real money.
          </strong>{" "}
          Orders placed now are live at Angel One.
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Equity"
          value={account ? money(account.equity) : "—"}
          hint={account ? `peak ${money(account.peak_equity)}` : undefined}
          tone="brand"
        />
        <StatTile
          label="Day P&L"
          value={account ? signedMoney(account.day_pl) : "—"}
          delta={account ? { text: `${account.day_pl_pct.toFixed(2)}%`, value: account.day_pl } : undefined}
        />
        <StatTile
          label="Engines running"
          value={fleet ? `${fleet.running} / ${fleet.total}` : "—"}
          hint={fleet ? `${fleet.live_running} on real money` : undefined}
        />
        <StatTile
          label="Feed"
          value={connection === "live" ? "Live" : connection === "polling" ? "Polling" : "Offline"}
          hint={snapshot?.engine.phase ?? "no engine reporting"}
        />
      </div>

      <section aria-labelledby="fleet-heading" className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 id="fleet-heading" className="text-2xs font-semibold uppercase tracking-[0.16em] text-brand">
            Algorithms
          </h2>
          <Badge tone={list.length ? "neutral" : "warning"}>
            {list.length} registered
          </Badge>
        </div>

        {!list.length ? (
          <Empty>No algorithms registered yet.</Empty>
        ) : (
          <div
            className={`grid gap-4 ${
              single ? "grid-cols-1" : "sm:grid-cols-2 xl:grid-cols-3"
            }`}
          >
            {list.map((algo) => (
              <AlgoCard
                key={algo.id}
                algo={algo}
                // The shared snapshot belongs to whichever engine published it;
                // only attribute it to that algorithm.
                snapshot={snapshot?.engine.pid === algo.runtime.pid ? snapshot : null}
                busy={busy === algo.id}
                compact={!single && list.length > 4}
                onStart={() => void control(algo.id, "start")}
                onStop={() => void control(algo.id, "stop")}
              />
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-5 xl:grid-cols-[3fr_2fr]">
        <div className="space-y-5">
          <Card title="NIFTY 50" subtitle="Live price action from TradingView">
            <TradingViewChart height={380} />
          </Card>

          <Card
            title="Equity and fills"
            subtitle="Arrows mark entries, circles mark exits coloured by outcome"
          >
            <MarkedChart equity={equity} trades={trades} height={280} />
          </Card>
        </div>

        <div className="space-y-5">
          <HealthStrip />
          <LiveTape events={events.slice(0, 60)} height={300} />
          <NewsPanel limit={10} />
        </div>
      </div>
    </div>
  );
}
