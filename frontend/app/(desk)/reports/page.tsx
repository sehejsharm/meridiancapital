"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { LazyMarkedChart } from "@/components/LazyMarkedChart";
import { Badge, Button, Card, Empty, StatTile } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { fetchAlgos } from "@/lib/algos";
import { istDateTime, istTime, money, pnlClass, signedMoney } from "@/lib/format";
import type { AlgoList, ReportPayload } from "@/lib/types";

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * Results.
 *
 * The page shows the same payload the CSV and PDF are rendered from, so what is
 * on screen and what downloads cannot disagree. Downloads go through a normal
 * link to the Next relay rather than a fetch-and-blob, which keeps the session
 * cookie doing the authenticating and lets the browser name the file.
 */
export default function ReportsPage() {
  const [algos, setAlgos] = useState<AlgoList | null>(null);
  const [algoId, setAlgoId] = useState<string>("");
  const [start, setStart] = useState(isoDaysAgo(30));
  const [end, setEnd] = useState(isoDaysAgo(0));
  const [report, setReport] = useState<ReportPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetchAlgos().then(setAlgos).catch(() => setAlgos(null));
  }, []);

  const query = useMemo(() => {
    const p = new URLSearchParams({ start, end });
    if (algoId) p.set("algo_id", algoId);
    return p.toString();
  }, [start, end, algoId]);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await apiGet<ReportPayload>(`/reports?${query}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not build the report");
      setReport(null);
    } finally {
      setBusy(false);
    }
  }, [query]);

  useEffect(() => {
    void run();
    // Only on first mount; afterwards the operator chooses when to re-run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const s = report?.summary;

  return (
    <div className="space-y-5">
      <Card title="Report" subtitle="Everything recorded for the period, on screen and in the download">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-[0.12em] text-ink-muted">Algorithm</span>
            <select
              value={algoId}
              onChange={(e) => setAlgoId(e.target.value)}
              className="rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
            >
              <option value="">All algorithms</option>
              {(algos?.algos ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-[0.12em] text-ink-muted">From</span>
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-[0.12em] text-ink-muted">To</span>
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
            />
          </label>
          <div className="flex items-end gap-2">
            <Button variant="primary" onClick={run} disabled={busy}>
              {busy ? "Building…" : "Run"}
            </Button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-hairline pt-4">
          <span className="text-2xs text-ink-muted">Download</span>
          <a
            href={`/api/proxy/reports.csv?${query}`}
            className="inline-flex items-center rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-brand/50 touch:min-h-[44px] touch:px-4"
          >
            CSV
          </a>
          <a
            href={`/api/proxy/reports.pdf?${query}`}
            className="inline-flex items-center rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-brand/50 touch:min-h-[44px] touch:px-4"
          >
            PDF
          </a>
        </div>
      </Card>

      {error && (
        <div role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">
          {error}
        </div>
      )}

      {s && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label="Net P&L" value={signedMoney(s.net_pnl)} tone="brand" hint={`${s.trades} closed trades`} />
            <StatTile label="Win rate" value={`${s.win_rate_pct.toFixed(1)}%`} hint={`${s.wins}W / ${s.losses}L`} />
            <StatTile
              label="Profit factor"
              value={s.profit_factor === null ? "n/a" : s.profit_factor.toFixed(2)}
              hint={`${money(s.gross_profit)} vs ${money(s.gross_loss)}`}
            />
            <StatTile label="Max drawdown" value={`${s.max_drawdown_pct.toFixed(2)}%`} hint={`${s.errors} errors logged`} />
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label="Average trade" value={signedMoney(s.average_trade)} />
            <StatTile label="Largest win" value={signedMoney(s.largest_win)} />
            <StatTile label="Largest loss" value={signedMoney(s.largest_loss)} />
            <StatTile label="Charges paid" value={money(s.total_charges)} hint={`${s.open_trades} still open`} />
          </div>

          <Card title="Equity and fills" subtitle="The period's curve with every entry and exit marked">
            <LazyMarkedChart equity={report.equity} trades={report.trades} height={300} />
          </Card>

          <Card title="Blotter" subtitle={`${report.trades.length} trades in the period`}>
            {!report.trades.length ? (
              <Empty>No trades in this window.</Empty>
            ) : (
              <div className="-mx-4 overflow-x-auto px-4">
                <table className="w-full min-w-[860px] text-xs">
                  <thead>
                    <tr className="border-b border-hairline text-left text-2xs uppercase tracking-[0.12em] text-ink-muted">
                      <th className="py-2 pr-3 font-medium">Date</th>
                      <th className="py-2 pr-3 font-medium">Mode</th>
                      <th className="py-2 pr-3 font-medium">Contract</th>
                      <th className="py-2 pr-3 text-right font-medium">Lots</th>
                      <th className="py-2 pr-3 text-right font-medium">Entry</th>
                      <th className="py-2 pr-3 text-right font-medium">Exit</th>
                      <th className="py-2 pr-3 text-right font-medium">Charges</th>
                      <th className="py-2 pr-3 text-right font-medium">Net</th>
                      <th className="py-2 font-medium">Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-hairline">
                    {report.trades.map((t) => (
                      <tr key={t.id}>
                        <td className="py-2 pr-3 text-ink-secondary">{t.session_date}</td>
                        <td className="py-2 pr-3">
                          <Badge tone={t.mode === "live" ? "critical" : "neutral"}>{t.mode}</Badge>
                        </td>
                        <td className="py-2 pr-3 font-mono text-2xs text-ink">{t.tsym ?? "—"}</td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-secondary">{t.lots ?? "—"}</td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-secondary">
                          {t.entry_prem?.toFixed(2) ?? "—"}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-secondary">
                          {t.exit_prem?.toFixed(2) ?? "open"}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums text-ink-muted">
                          {t.charges?.toFixed(0) ?? "—"}
                        </td>
                        <td className={`py-2 pr-3 text-right font-medium tabular-nums ${pnlClass(t.net)}`}>
                          {t.net === null ? "—" : signedMoney(t.net)}
                        </td>
                        <td className="py-2 text-ink-secondary">{t.reason ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="Event log" subtitle={`${report.events.length} entries, newest first`}>
            {!report.events.length ? (
              <Empty>No events in this window.</Empty>
            ) : (
              <ul className="max-h-96 divide-y divide-hairline overflow-y-auto">
                {report.events.slice(0, 300).map((e) => (
                  <li key={e.id} className="flex gap-3 py-2">
                    <span className="shrink-0 tabular-nums text-2xs text-ink-muted">{istTime(e.ts)}</span>
                    <span className="min-w-0 flex-1 text-2xs text-ink-secondary">{e.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <p className="text-2xs text-ink-muted">
            Generated {istDateTime(report.generated_at)} · {report.algo_id} · {report.start} to {report.end}
          </p>
        </>
      )}
    </div>
  );
}
