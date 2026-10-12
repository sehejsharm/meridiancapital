"use client";

import { useEffect, useState } from "react";

import { EquityChart, type ChartPoint } from "@/components/EquityChart";
import { Badge, Card, Empty, StatTile } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { minutes, money, percent, signedMoney, signedPercent, tradeTime } from "@/lib/format";
import type { DailyEquityPoint, TradeRow, TradeStats } from "@/lib/types";

export default function BlotterPage() {
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [daily, setDaily] = useState<DailyEquityPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const [blotter, equity] = await Promise.all([
          apiGet<{ trades: TradeRow[]; stats: TradeStats }>("/trades?limit=500"),
          apiGet<{ daily: DailyEquityPoint[] }>("/equity"),
        ]);
        setTrades(blotter.trades);
        setStats(blotter.stats);
        setDaily(equity.daily);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "could not load the blotter");
      } finally {
        setLoading(false);
      }
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, []);

  const dailyPoints: ChartPoint[] = daily.map((d) => ({
    label: d.session_date,
    value: d.equity,
    secondary: d.day_pl ?? undefined,
  }));
  const provisionalDay = daily.find((d) => d.source === "recorded");

  return (
    <div className="space-y-5">
      {stats && (
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            label="Net P&L, real money"
            value={signedMoney(stats.net)}
            delta={{ text: `${signedMoney(stats.gross)} before charges`, value: stats.gross }}
            hint={`${money(stats.charges)} in charges · ${stats.reconciled ?? 0} of ${stats.n} matched to Angel`}
            tone={stats.net > 0 ? "profit" : stats.net < 0 ? "loss" : "neutral"}
          />
          <StatTile
            label="Win rate"
            value={stats.n ? percent(stats.win_rate, 1) : "—"}
            hint={`${stats.wins} won · ${stats.losses} lost · ${stats.n} closed`}
          />
          <StatTile
            label="Best / worst trade"
            value={signedMoney(stats.best)}
            delta={{ text: `${signedMoney(stats.worst)} worst`, value: stats.worst }}
          />
          <StatTile label="Average hold" value={minutes(stats.avg_hold)} />
        </section>
      )}

      <Card
        title="Daily balance at Angel"
        subtitle={
          provisionalDay
            ? `Each session's closing balance: the next morning's opening ledger balance. ${provisionalDay.session_date} is provisional until tomorrow's opening is read.`
            : "Each session's closing balance: the next morning's opening ledger balance, and today's live trading balance"
        }
      >
        <EquityChart
          points={dailyPoints}
          emptyMessage="Two or more completed sessions are needed to draw this."
          xLabel={(p) => p.label}
        />
      </Card>

      <Card
        title="Trade blotter"
        subtitle="Every fill, with P&L as Angel One booked it"
        action={
          trades.length > 0 ? <Badge tone="neutral">{trades.length} records</Badge> : undefined
        }
      >
        {error ? (
          <Empty>{error}</Empty>
        ) : loading ? (
          <Empty>Loading…</Empty>
        ) : trades.length === 0 ? (
          <Empty>
            No trades recorded yet. The engine takes at most one a day, and only on a qualifying
            breakout.
          </Empty>
        ) : (
          <>
          {/* Phones: a trade reads as a card — the result and net up top where
              the eye lands, the mechanics underneath. Twelve columns sideways is
              two and a half screens of scrolling to find out whether you won. */}
          <ul className="space-y-2.5 lg:hidden">
            {trades.map((t) => {
              const open = !t.exit_ts;
              const net = t.net_angel ?? t.net;
              const won = (net ?? 0) > 0;
              return (
                <li key={t.id} className="tnum rounded-lg border border-hairline bg-surface-raised p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                      {open ? (
                        <Badge tone="brand">Open</Badge>
                      ) : (
                        <Badge tone={won ? "good" : "critical"}>{won ? "Win" : "Loss"}</Badge>
                      )}
                      <span className="whitespace-nowrap text-sm font-medium text-ink">
                        {t.strike} {t.side}
                      </span>
                      {t.mode === "paper" && (
                        <span className="text-2xs uppercase text-ink-muted">paper</span>
                      )}
                    </div>
                    <span
                      className={`shrink-0 text-base font-semibold ${
                        open ? "text-ink-muted" : won ? "text-profit" : "text-loss"
                      }`}
                    >
                      {open ? "—" : signedMoney(net)}
                      {t.pnl_source === "estimate" && <span className="ml-0.5 text-warning">~</span>}
                    </span>
                  </div>
                  {!open && t.mode === "live" && <Matched t={t} className="mt-1 justify-end" />}
                  <dl className="mt-2.5 grid grid-cols-3 gap-x-3 gap-y-2 text-2xs">
                    <MiniField label="In" value={t.entry_prem?.toFixed(2) ?? "—"} />
                    <MiniField label="Out" value={t.exit_prem?.toFixed(2) ?? "—"} />
                    <MiniField
                      label="Peak"
                      value={t.peak_pct != null ? signedPercent(t.peak_pct / 100, 0) : "—"}
                    />
                    <MiniField label="Lots" value={String(t.lots ?? "—")} />
                    <MiniField label="Held" value={minutes(t.hold_min)} />
                    <MiniField
                      label="Charges"
                      value={(t.charges_angel ?? t.charges) != null ? money(t.charges_angel ?? t.charges) : "—"}
                    />
                  </dl>
                  <p className="mt-2.5 border-t border-hairline pt-2 text-2xs text-ink-muted">
                    {tradeTime(t.entry_ts)}
                    {t.exit_ts ? ` → ${tradeTime(t.exit_ts)}` : ""}
                    {t.reason ? ` · ${t.reason}` : ""}
                  </p>
                </li>
              );
            })}
          </ul>

          <div className="-mx-4 hidden overflow-x-auto px-4 lg:block">
            <table className="w-full min-w-[56rem] border-collapse text-xs">
              <thead>
                <tr className="border-b border-hairline text-left text-2xs uppercase tracking-[0.12em] text-ink-muted">
                  <th className="py-2 pr-3 font-medium">Result</th>
                  <th className="py-2 pr-3 font-medium">Entry</th>
                  <th className="py-2 pr-3 font-medium">Exit</th>
                  <th className="py-2 pr-3 font-medium">Contract</th>
                  <th className="py-2 pr-3 text-right font-medium">Lots</th>
                  <th className="py-2 pr-3 text-right font-medium">In</th>
                  <th className="py-2 pr-3 text-right font-medium">Out</th>
                  <th className="py-2 pr-3 text-right font-medium">Peak</th>
                  <th className="py-2 pr-3 text-right font-medium">Charges</th>
                  <th className="py-2 pr-3 text-right font-medium">Net</th>
                  <th className="py-2 pr-3 font-medium">Why</th>
                  <th className="py-2 text-right font-medium">Held</th>
                </tr>
              </thead>
              <tbody className="tnum">
                {trades.map((t) => {
                  const open = !t.exit_ts;
                  const net = t.net_angel ?? t.net;
                  const charges = t.charges_angel ?? t.charges;
                  const won = (net ?? 0) > 0;
                  return (
                    <tr key={t.id} className="border-b border-hairline last:border-0">
                      <td className="py-2 pr-3">
                        {open ? (
                          <Badge tone="brand">Open</Badge>
                        ) : (
                          <Badge tone={won ? "good" : "critical"}>{won ? "Win" : "Loss"}</Badge>
                        )}
                      </td>
                      <td className="whitespace-nowrap py-2 pr-3 text-ink-secondary">{tradeTime(t.entry_ts)}</td>
                      <td className="whitespace-nowrap py-2 pr-3 text-ink-secondary">{tradeTime(t.exit_ts)}</td>
                      <td className="py-2 pr-3 text-ink">
                        {t.strike} {t.side}
                        {t.mode === "paper" && (
                          <span className="ml-1.5 text-2xs uppercase text-ink-muted">paper</span>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-right text-ink-secondary">{t.lots ?? "—"}</td>
                      <td className="py-2 pr-3 text-right text-ink-secondary">
                        {t.entry_prem?.toFixed(2) ?? "—"}
                      </td>
                      <td className="py-2 pr-3 text-right text-ink-secondary">
                        {t.exit_prem?.toFixed(2) ?? "—"}
                      </td>
                      <td className="py-2 pr-3 text-right text-ink-secondary">
                        {t.peak_pct != null ? signedPercent(t.peak_pct / 100, 0) : "—"}
                      </td>
                      <td className="whitespace-nowrap py-2 pr-3 text-right text-ink-muted">
                        {charges != null ? money(charges) : "—"}
                      </td>
                      <td
                        className={`whitespace-nowrap py-2 pr-3 text-right font-semibold ${
                          open ? "text-ink-muted" : won ? "text-profit" : "text-loss"
                        }`}
                      >
                        {open ? "—" : signedMoney(net)}
                        {t.pnl_source === "estimate" && (
                          <span
                            className="ml-1 text-warning"
                            title="Angel was unreachable at exit — this figure is an estimate"
                          >
                            ~
                          </span>
                        )}
                        {!open && t.mode === "live" && <Matched t={t} className="mt-0.5 justify-end" />}
                      </td>
                      <td className="py-2 pr-3 text-ink-secondary">{t.reason ?? "—"}</td>
                      <td className="py-2 text-right text-ink-secondary">
                        {minutes(t.hold_min)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
            <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
              P&amp;L before charges is Angel&apos;s position book. Charges and net are Angel&apos;s own:{" "}
              <span className="text-good">✓ Angel ledger</span> is the change in your Angel balance from that
              morning&apos;s opening to the next, to the paisa;{" "}
              <span className="text-good">✓ Angel balance</span> is your trading balance right after the trade;{" "}
              <span className="text-ink-secondary">provisional</span> is the program&apos;s own figure until the
              next morning&apos;s opening balance confirms it. A <span className="text-warning">~</span> marks a
              trade Angel could not be reached for at exit.
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

/** Whether a real-money trade's charges and net are Angel's own figures. */
function Matched({ t, className = "" }: { t: TradeRow; className?: string }) {
  if (t.ledger_gap != null && Math.abs(t.ledger_gap) >= 0.01) {
    return (
      <span
        className={`flex items-center gap-1 text-2xs font-normal text-warning ${className}`}
        title="Your Angel balance moved by more than this trade's charges can explain — a deposit or a withdrawal. The program's figure is kept."
      >
        {signedMoney(t.ledger_gap)} moved outside trading
      </span>
    );
  }
  if (t.reconciled === "Angel ledger" || t.reconciled === "Angel balance") {
    return (
      <span
        className={`flex items-center gap-1 text-2xs font-normal text-good ${className}`}
        title={
          t.reconciled === "Angel ledger"
            ? "Charges and net from the change in your Angel balance, this morning's opening to the next"
            : "Charges and net from your Angel trading balance right after the trade"
        }
      >
        ✓ {t.reconciled}
      </span>
    );
  }
  return (
    <span
      className={`flex items-center gap-1 text-2xs font-normal text-ink-muted ${className}`}
      title="The program's own figure; it is matched to Angel's ledger once the next morning's opening balance is read"
    >
      provisional
    </span>
  );
}

function MiniField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="uppercase tracking-[0.1em] text-ink-muted">{label}</dt>
      <dd className="mt-0.5 truncate text-xs text-ink-secondary">{value}</dd>
    </div>
  );
}
