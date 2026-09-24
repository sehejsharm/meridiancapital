"use client";

import { useCallback, useEffect, useState } from "react";

import { AlgoCard } from "@/components/AlgoCard";
import { HealthStrip } from "@/components/HealthStrip";
import { LiveTape } from "@/components/LiveTape";
import { LazyMarkedChart } from "@/components/LazyMarkedChart";
import { NewsPanel } from "@/components/NewsPanel";
import { NiftyTicker } from "@/components/NiftyTicker";
import { RateGauges } from "@/components/RateGauges";
import { RunModeDialog } from "@/components/RunModeDialog";
import { StalenessMonitor } from "@/components/StalenessMonitor";
import { TradingViewChart } from "@/components/TradingViewChart";
import { NiftyChart } from "@/components/NiftyChart";
import { OptionChain } from "@/components/OptionChain";
import { Badge, Card, Empty, StatTile } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { fetchAlgos } from "@/lib/algos";
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
  // TradingView's free widget does not reliably carry NSE's NIFTY, so the
  // chart drawn from Angel's own candles is the default.
  const [chartView, setChartView] = useState<"native" | "tradingview">("native");
  const [notice, setNotice] = useState<string | null>(null);

  const loadAlgos = useCallback(async () => {
    try {
      setAlgos(await fetchAlgos());
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
    async (algoId: string, action: "start" | "stop", mode?: "paper" | "live") => {
      setBusy(algoId);
      setNotice(null);
      try {
        await apiPost(`/algos/${algoId}/${action}`, mode ? { mode } : undefined);
        await loadAlgos();
      } catch (e) {
        setNotice(e instanceof Error ? e.message : `could not ${action} ${algoId}`);
      } finally {
        setBusy(null);
      }
    },
    [loadAlgos],
  );

  // Starting is never one tap: the desk asks which money this will trade.
  const [asking, setAsking] = useState<string | null>(null);
  const pickMode = useCallback(
    async (mode: "paper" | "live") => {
      const algoId = asking;
      setAsking(null);
      if (algoId) await control(algoId, "start", mode);
    },
    [asking, control],
  );

  const fleet = status?.fleet;
  const list = algos?.algos ?? [];
  const single = list.length <= 1;
  const account = snapshot?.account;

  return (
    <div className="space-y-5">
      <RunModeDialog
        name={list.find((a) => a.id === asking)?.name ?? "this algorithm"}
        open={asking !== null}
        busy={busy !== null}
        onPick={(mode) => void pickMode(mode)}
        onCancel={() => setAsking(null)}
      />

      <StalenessMonitor />
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

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
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
        {/* The dashboard's link to the server — not a trading mode, so it no
            longer says "Live" beside a desk that is trading paper. */}
        <StatTile
          label="Data link"
          value={connection === "live" ? "Connected" : connection === "polling" ? "Polling" : "Offline"}
          hint={
            snapshot?.engine.phase
              ? `engine ${snapshot.engine.phase.toLowerCase()}`
              : "no engine running"
          }
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
          <div className={`grid gap-4 ${fleetColumns(list.length)}`}>
            {list.map((algo) => (
              <AlgoCard
                key={algo.id}
                algo={algo}
                // The shared snapshot belongs to whichever engine published it;
                // only attribute it to that algorithm.
                snapshot={snapshot?.engine.pid === algo.runtime.pid ? snapshot : null}
                busy={busy === algo.id}
                compact={!single && list.length > 4}
                wide={single ? "md" : list.length === 2 ? "xl" : undefined}
                onStart={() => setAsking(algo.id)}
                onStop={() => void control(algo.id, "stop")}
              />
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-5 xl:grid-cols-[3fr_2fr]">
        <div className="space-y-5">
          <Card
            title="NIFTY 50"
            subtitle={chartView === "native" ? "Today, one-minute candles" : "TradingView"}
            action={
              <div className="flex rounded-md border border-hairline p-0.5 text-2xs" role="tablist">
                {(["native", "tradingview"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="tab"
                    aria-selected={chartView === v}
                    onClick={() => setChartView(v)}
                    className={`rounded px-2.5 py-1 uppercase tracking-[0.1em] transition-colors touch:min-h-[36px] ${
                      chartView === v ? "bg-brand-dim text-brand" : "text-ink-muted hover:text-ink"
                    }`}
                  >
                    {v === "native" ? "Chart" : "TradingView"}
                  </button>
                ))}
              </div>
            }
          >
            {chartView === "native" ? <NiftyChart height={380} /> : <TradingViewChart height={380} />}
          </Card>

          <OptionChain />

          <Card
            title="Equity and fills"
            subtitle="Arrows mark entries, circles mark exits coloured by outcome"
          >
            <LazyMarkedChart equity={equity} trades={trades} height={280} />
          </Card>
        </div>

        <div className="space-y-5">
          <HealthStrip />
          <RateGauges api={snapshot?.health?.api} />
          <LiveTape events={events.slice(0, 60)} height={300} />
          <NewsPanel limit={10} />
        </div>
      </div>
    </div>
  );
}

/**
 * Columns for the algorithm cards: as many as there are cards, up to what the
 * screen can hold, so two algorithms fill a laptop's width instead of sitting
 * in the left half of a four-column grid.
 *
 * grid-cols-1 on phones is load-bearing: without it the implicit column sizes
 * to the longest line in a card and the whole page scrolls sideways.
 */
function fleetColumns(n: number): string {
  if (n <= 1) return "grid-cols-1";
  if (n === 2) return "grid-cols-1 sm:grid-cols-2";
  if (n === 3) return "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3";
  return "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4";
}
