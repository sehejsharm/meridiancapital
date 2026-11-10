"use client";

import { useEffect, useMemo, useState } from "react";

import { useProgramConfigs } from "@/components/ProgramConfig";
import { Sparkline } from "@/components/Sparkline";
import { Badge, Card, Empty, Skeleton, StatTile } from "@/components/ui";
import {
  byDaysToExpiry, byEntryHour, byWeekday, calendarWeeks, closedTrades, dailyPnl, kelly, monteCarlo,
  rollingSortino, rollingWinRate, underwater, type Bucket,
} from "@/lib/analytics";
import { apiGet } from "@/lib/client-api";
import { dayLabel, money, percent, signedMoney } from "@/lib/format";
import { useDeskSnapshot } from "@/lib/recorded";
import type { TradeRow } from "@/lib/types";

/**
 * Where the money comes from: every real-money trade on the blotter, cut by
 * day, hour and weekday, with the drawdown under the curve and what the same
 * trades say about the road ahead. Pure aggregation of figures the blotter
 * already shows (Angel's net where reconciled), so each number can be checked.
 */
export default function AnalyticsPage() {
  const [rows, setRows] = useState<TradeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const configs = useProgramConfigs();
  const { snapshot } = useDeskSnapshot();

  useEffect(() => {
    apiGet<{ trades: TradeRow[] }>("/trades?limit=1000")
      .then((d) => setRows(d.trades ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : "could not load the blotter"));
  }, []);

  const trades = useMemo(() => closedTrades(rows ?? []), [rows]);
  const daily = useMemo(() => dailyPnl(trades), [trades]);
  const curve = useMemo(() => underwater(trades), [trades]);
  const winRate = useMemo(() => rollingWinRate(trades, 10), [trades]);
  const sortino = useMemo(() => rollingSortino(trades, 20), [trades]);

  // The live program's own kill switches (as it logged them), else the built-in's.
  const live = configs?.programs.find((p) => p.mode === "live") ?? configs?.programs[0];
  const val = (k: string, fallback: number) => {
    const r = live?.rows.find((x) => x.key === k);
    const v = r?.running_known ? r.running : r?.code;
    return typeof v === "number" ? v : fallback;
  };
  const capital = snapshot?.account?.money?.balance ?? snapshot?.account?.equity ?? 0;
  const sim = useMemo(
    () =>
      trades.length >= 5 && capital > 0
        ? monteCarlo(trades.map((t) => t.net), {
            horizon: 60, paths: 2000, capital,
            dailyKill: val("DAILY_LOSS_LIMIT_RS", 12000),
            weeklyKill: val("WEEKLY_LOSS_LIMIT_RS", 25000),
            ddFrac: val("MAX_DRAWDOWN_STOP", 0.45),
          })
        : null,
    [trades, capital, live],
  );
  const k = useMemo(() => kelly(trades, 30), [trades]);

  if (error) {
    return <div role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">{error}</div>;
  }
  if (!rows) return <Skeleton rows={8} />;
  if (!trades.length) return <Empty>No closed real-money trades yet.</Empty>;

  const net = curve[curve.length - 1]?.cum ?? 0;
  const maxDd = Math.min(0, ...curve.map((p) => p.drawdown));
  const wins = trades.filter((t) => t.net > 0).length;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatTile label="Trading P&L, real money" value={signedMoney(net)} tone={net >= 0 ? "profit" : "loss"}
          hint={`${trades.length} trades · ${daily.size} sessions`} />
        <StatTile label="Win rate" value={percent(wins / trades.length, 1)} hint={`${wins} won · ${trades.length - wins} lost`} />
        <StatTile label="Deepest drawdown" value={signedMoney(maxDd)} hint="Trading only, from the running peak" />
        <StatTile label="Rolling win rate (10)" value={winRate[winRate.length - 1] != null ? percent(winRate[winRate.length - 1], 0) : "—"}
          hint={sortino[sortino.length - 1] != null ? `rolling Sortino (20): ${sortino[sortino.length - 1]}` : "Sortino needs 20 trades"} />
      </div>

      <Card title="Equity and drawdown" subtitle="Trading P&L added up trade by trade; the shaded area below is the drawdown from the running peak">
        <EquityUnderwater curve={curve} />
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Rolling win rate" subtitle="Share of the last 10 trades that won">
          <Sparkline values={winRate.map((v) => (v == null ? null : v * 100))} height={90} className="text-brand" label="Rolling win rate" />
        </Card>
        <Card title="Rolling Sortino" subtitle="Mean result over downside deviation, last 20 trades">
          {trades.length < 20 ? <Empty>Needs 20 trades.</Empty> : <Sparkline values={sortino} height={90} className="text-good" label="Rolling Sortino" />}
        </Card>
      </div>

      <Card title="Daily P&L calendar" subtitle="Each square a trading day, Monday to Friday; green made money, red lost it, empty no trade">
        <Heatmap daily={daily} />
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="By entry hour" subtitle="When the trade was entered, IST">
          <Buckets buckets={byEntryHour(trades)} />
        </Card>
        <Card title="By weekday" subtitle="Which day of the week the trade was taken">
          <Buckets buckets={byWeekday(trades)} />
        </Card>
      </div>

      <Card
        title="By days to expiry"
        subtitle="How many days the contract had left when it was bought. The backtest holds no 7–8-day contracts, so this is the only record of them"
      >
        <Buckets buckets={byDaysToExpiry(trades)} />
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card
          title="Risk of ruin — Monte Carlo"
          subtitle={sim ? `${sim.paths.toLocaleString("en-IN")} paths of the next ${sim.horizon} sessions, resampling the ${trades.length} trades so far` : undefined}
          action={<Badge tone="neutral">a guide, not a forecast</Badge>}
        >
          {!sim ? (
            <Empty>Needs at least 5 trades and a known account balance.</Empty>
          ) : (
            <dl className="grid grid-cols-2 gap-3 text-xs">
              <Stat label={`Daily kill (−${money(val("DAILY_LOSS_LIMIT_RS", 12000))}) hit`} value={percent(sim.pDailyKill, 1)} warn={sim.pDailyKill > 0.2} />
              <Stat label={`Weekly kill (−${money(val("WEEKLY_LOSS_LIMIT_RS", 25000))}) hit`} value={percent(sim.pWeeklyKill, 1)} warn={sim.pWeeklyKill > 0.2} />
              <Stat label={`Drawdown ≥ ${percent(val("MAX_DRAWDOWN_STOP", 0.45), 0)} of ${money(capital)}`} value={percent(sim.pDrawdown, 1)} warn={sim.pDrawdown > 0.05} />
              <Stat label="Down after the period" value={percent(sim.pLoss, 1)} warn={sim.pLoss > 0.3} />
              <Stat label="Result, 5th / 50th / 95th percentile" value={`${signedMoney(sim.final.p5)} / ${signedMoney(sim.final.p50)} / ${signedMoney(sim.final.p95)}`} />
              <Stat label="Worst drawdown, median / 95th" value={`${money(sim.worstDrawdown.p50)} / ${money(sim.worstDrawdown.p95)}`} />
            </dl>
          )}
        </Card>

        <Card title="Position-sizing reference (Kelly)" subtitle="From the last 30 trades. A reference beside the Sizing settings — nothing is applied">
          {!k ? (
            <Empty>Needs 10 trades with both wins and losses.</Empty>
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-3 text-xs">
                <Stat label="Win rate" value={percent(k.winRate, 0)} />
                <Stat label="Average win / loss" value={`${money(k.avgWin)} / ${money(k.avgLoss)}`} />
                <Stat label="Payoff ratio" value={k.payoff.toFixed(2)} />
                <Stat label="Kelly / half-Kelly" value={k.kelly > 0 ? `${percent(k.kelly, 1)} / ${percent(k.half, 1)}` : "no edge — 0%"} warn={k.kelly <= 0} />
              </dl>
              <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
                Kelly is the share of capital to <em>risk</em> per trade (the loss at the stop), not the share deployed.
                With a {percent(val("STOP_FRAC", 0.45), 0)} stop on {percent(val("DEPLOY_FRACTION", 0.4), 0)} deployed, today&apos;s
                risk per trade is about {percent(val("STOP_FRAC", 0.45) * val("DEPLOY_FRACTION", 0.4), 0)} of equity
                {k.kelly > 0 ? `, against half-Kelly ${percent(k.half, 1)}` : ""}. Thirty trades is a small sample; treat it as a sanity check.
              </p>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}

function Stat({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="min-w-0 rounded-md border border-hairline px-3 py-2">
      <dt className="text-2xs text-ink-muted">{label}</dt>
      <dd className={`mt-0.5 font-semibold tabular-nums ${warn ? "text-warning" : "text-ink"}`}>{value}</dd>
    </div>
  );
}

function EquityUnderwater({ curve }: { curve: ReturnType<typeof underwater> }) {
  const w = 100, h = 60, uh = 22;
  const hi = Math.max(0, ...curve.map((p) => p.cum));
  const lo = Math.min(0, ...curve.map((p) => p.cum));
  const span = hi - lo || 1;
  const dd = Math.min(-1, ...curve.map((p) => p.drawdown));
  const x = (i: number) => (curve.length < 2 ? 0 : (i / (curve.length - 1)) * w);
  const y = (v: number) => h - 2 - ((v - lo) / span) * (h - 4);
  const line = curve.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(p.cum).toFixed(2)}`).join(" ");
  const peak = curve.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(p.peak).toFixed(2)}`).join(" ");
  const under = curve.map((p, i) => `L${x(i).toFixed(2)},${((p.drawdown / dd) * (uh - 2)).toFixed(2)}`).join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="block h-52 w-full" role="img" aria-label="Cumulative trading P&L">
        <path d={peak} fill="none" stroke="currentColor" className="text-ink-muted" strokeDasharray="1.5 1.5" strokeWidth={0.8} vectorEffect="non-scaling-stroke" />
        <path d={line} fill="none" stroke="currentColor" className="text-brand" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
        <line x1={0} x2={w} y1={y(0)} y2={y(0)} stroke="currentColor" className="text-hairline" strokeWidth={0.6} vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="mt-1 text-2xs uppercase tracking-[0.12em] text-ink-muted">Underwater: below the peak</div>
      <svg viewBox={`0 0 ${w} ${uh}`} preserveAspectRatio="none" className="block h-16 w-full" role="img" aria-label="Drawdown from the running peak">
        <path d={`M0,0 ${under} L${w},0 Z`} fill="currentColor" className="text-loss" opacity={0.35} />
      </svg>
      <div className="mt-1 flex justify-between text-2xs text-ink-muted">
        <span>{dayLabel(curve[0]?.day)}</span>
        <span>{dayLabel(curve[curve.length - 1]?.day)}</span>
      </div>
    </div>
  );
}

function Heatmap({ daily }: { daily: Map<string, number> }) {
  const weeks = calendarWeeks(daily);
  const max = Math.max(1, ...[...daily.values()].map((v) => Math.abs(v)));
  // Whole class names, so Tailwind's scanner sees every one it must generate.
  const tone = (v: number | null) => {
    if (v === null) return "bg-surface-raised";
    if (v === 0) return "bg-series/40";
    const a = Math.abs(v) / max;
    if (v > 0) return a > 0.66 ? "bg-profit/90" : a > 0.33 ? "bg-profit/60" : "bg-profit/35";
    return a > 0.66 ? "bg-loss/90" : a > 0.33 ? "bg-loss/60" : "bg-loss/35";
  };
  return (
    // The weeks share the width there is (up to 14 px each) instead of scrolling
    // sideways: a long history draws narrower cells, never a scroll bar.
    <div className="flex gap-1">
      <div className="mr-1 flex shrink-0 flex-col gap-1 text-2xs text-ink-muted">
        {["Mon", "Tue", "Wed", "Thu", "Fri"].map((d) => <span key={d} className="h-3.5 leading-[0.875rem]">{d}</span>)}
      </div>
      <div className="flex min-w-0 flex-1 gap-[3px]">
        {weeks.map((week) => (
          <div key={week[0].day} className="flex min-w-0 max-w-[14px] flex-1 flex-col gap-1">
            {week.map((c) => (
              <span
                key={c.day}
                className={`block h-3.5 w-full rounded-sm ${tone(c.net)}`}
                title={`${dayLabel(c.day)}: ${c.net === null ? "no trade" : signedMoney(c.net)}`}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function Buckets({ buckets }: { buckets: Bucket[] }) {
  const max = Math.max(1, ...buckets.map((b) => Math.abs(b.net)));
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-hairline text-left text-2xs uppercase tracking-[0.12em] text-ink-muted">
          <th className="py-1.5 pr-2 font-medium" />
          <th className="py-1.5 pr-2 text-right font-medium">Trades</th>
          <th className="py-1.5 pr-2 text-right font-medium">Win</th>
          <th className="py-1.5 pr-2 text-right font-medium">Net</th>
          <th className="w-1/3 py-1.5 font-medium" />
        </tr>
      </thead>
      <tbody>
        {buckets.map((b) => (
          <tr key={b.key} className="border-b border-hairline last:border-0">
            <td className="py-1.5 pr-2 tabular-nums text-ink">{b.key}</td>
            <td className="py-1.5 pr-2 text-right tabular-nums text-ink-secondary">{b.n}</td>
            <td className="py-1.5 pr-2 text-right tabular-nums text-ink-secondary">{percent(b.winRate, 0)}</td>
            <td className={`py-1.5 pr-2 text-right tabular-nums ${b.net >= 0 ? "text-profit" : "text-loss"}`}>{signedMoney(b.net)}</td>
            <td className="py-1.5">
              <div className="h-2 rounded-sm bg-surface-raised">
                <div className={`h-2 rounded-sm ${b.net >= 0 ? "bg-profit/70" : "bg-loss/70"}`} style={{ width: `${(100 * Math.abs(b.net)) / max}%` }} />
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
