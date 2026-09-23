"use client";

import { useEffect, useState } from "react";

import { EquityChart, type ChartPoint } from "@/components/EquityChart";
import { Badge, Card, Empty, StatTile } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { minutes, money, percent, signedMoney, signedPercent } from "@/lib/format";
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
    secondary: d.day_pl,
  }));

  return (
    <div className="space-y-5">
      {stats && (
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            label="Net P&L, all time"
            value={signedMoney(stats.net)}
            delta={{ text: `${money(stats.gross)} gross`, value: stats.gross }}
            hint={`${money(stats.charges)} in charges`}
            tone="brand"
          />
          <StatTile
            label="Win rate"
            value={stats.n ? percent(stats.win_rate, 0) : "—"}
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

      <Card title="Daily equity" subtitle="Closing equity of each session">
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
          <ul className="space-y-2.5 md:hidden">
            {trades.map((t) => {
              const open = !t.exit_ts;
              const won = (t.net ?? 0) > 0;
              return (
                <li key={t.id} className="tnum rounded-lg border border-hairline bg-surface-raised p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      {open ? (
                        <Badge tone="brand">Open</Badge>
                      ) : (
                        <Badge tone={won ? "good" : "critical"}>{won ? "Win" : "Loss"}</Badge>
                      )}
                      <span className="truncate text-sm font-medium text-ink">
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
                      {open ? "—" : signedMoney(t.net)}
                      {t.pnl_source === "estimate" && <span className="ml-0.5 text-warning">~</span>}
                    </span>
                  </div>
                  <dl className="mt-2.5 grid grid-cols-3 gap-x-3 gap-y-2 text-2xs">
                    <MiniField label="In" value={t.entry_prem?.toFixed(2) ?? "—"} />
                    <MiniField label="Out" value={t.exit_prem?.toFixed(2) ?? "—"} />
                    <MiniField
                      label="Peak"
                      value={t.peak_pct != null ? signedPercent(t.peak_pct / 100, 0) : "—"}
                    />
                    <MiniField label="Lots" value={String(t.lots ?? "—")} />
                    <MiniField label="Held" value={minutes(t.hold_min)} />
                    <MiniField label="Charges" value={t.charges != null ? money(t.charges) : "—"} />
                  </dl>
                  <p className="mt-2.5 border-t border-hairline pt-2 text-2xs text-ink-muted">
                    {t.entry_ts}
                    {t.exit_ts ? ` → ${t.exit_ts}` : ""}
                    {t.reason ? ` · ${t.reason}` : ""}
                  </p>
                </li>
              );
            })}
          </ul>

          <div className="-mx-4 hidden overflow-x-auto px-4 md:block">
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
                  const won = (t.net ?? 0) > 0;
                  return (
                    <tr key={t.id} className="border-b border-hairline last:border-0">
                      <td className="py-2 pr-3">
                        {open ? (
                          <Badge tone="brand">Open</Badge>
                        ) : (
                          <Badge tone={won ? "good" : "critical"}>{won ? "Win" : "Loss"}</Badge>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-ink-secondary">{t.entry_ts}</td>
                      <td className="py-2 pr-3 text-ink-secondary">{t.exit_ts ?? "—"}</td>
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
                      <td className="py-2 pr-3 text-right text-ink-muted">
                        {t.charges != null ? money(t.charges) : "—"}
                      </td>
                      <td
                        className={`py-2 pr-3 text-right font-semibold ${
                          open ? "text-ink-muted" : won ? "text-profit" : "text-loss"
                        }`}
                      >
                        {open ? "—" : signedMoney(t.net)}
                        {t.pnl_source === "estimate" && (
                          <span
                            className="ml-1 text-warning"
                            title="Angel was unreachable at exit — this figure is an estimate"
                          >
                            ~
                          </span>
                        )}
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
            <p className="mt-3 text-2xs text-ink-muted">
              A <span className="text-warning">~</span> marks a trade whose P&amp;L is a local
              estimate because Angel One was unreachable at exit. Every other figure is Angel&apos;s
              own, net of real charges.
            </p>
          </>
        )}
      </Card>
    </div>
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
