"use client";

import { useCallback, useEffect, useState } from "react";

import { Card, Skeleton, StatTile } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { dayLabel, money, signedMoney } from "@/lib/format";
import type { TaxReport } from "@/lib/types";

const LINK =
  "inline-flex items-center rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-brand/50 touch:min-h-[44px] touch:px-4";

/**
 * The financial year's F&O P&L for the CA: real-money trades closed in the
 * year, Angel's charges, turnover the way ICAI counts it for options, and the
 * realised P&L at each advance-tax date. The download carries every trade.
 */
export function TaxPnl() {
  const [fy, setFy] = useState<string | null>(null);
  const [report, setReport] = useState<TaxReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (year: string | null) => {
    setError(null);
    try {
      const r = await apiGet<TaxReport>(`/tax${year ? `?fy=${year}` : ""}`);
      setReport(r);
      setFy(r.fy);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not build the tax report");
    }
  }, []);

  useEffect(() => {
    void load(null);
  }, [load]);

  const t = report?.totals;
  return (
    <Card
      title="Tax P&L for your CA"
      subtitle="Real-money F&O trades by financial year, with Angel's charges and turnover"
      action={
        report && (
          <select
            aria-label="Financial year"
            value={fy ?? ""}
            onChange={(e) => {
              setReport(null);
              void load(e.target.value);
            }}
            className="rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
          >
            {report.years.map((y) => (
              <option key={y} value={y}>
                FY {y}
              </option>
            ))}
          </select>
        )
      }
    >
      {error ? (
        <p role="alert" className="text-xs text-critical">{error}</p>
      ) : !report || !t ? (
        <Skeleton rows={4} />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <StatTile label="Net P&L" value={signedMoney(t.net_pnl)} tone={t.net_pnl > 0 ? "profit" : t.net_pnl < 0 ? "loss" : "neutral"} hint={`${t.trades} trades closed`} />
            <StatTile label="Turnover" value={money(t.turnover)} hint="sum of absolute P&L per trade" />
            <StatTile label="Charges" value={money(t.charges)} hint="brokerage, STT, fees, GST, stamp" />
            <StatTile label="Gross P&L" value={signedMoney(t.gross_pnl)} hint={`${money(t.gross_profit)} profit, ${money(t.gross_loss)} loss`} />
          </div>

          <div>
            <div className="mb-1.5 text-2xs uppercase tracking-[0.14em] text-ink-muted">Realised at each advance-tax date</div>
            <table className="w-full text-xs">
              <tbody className="divide-y divide-hairline">
                {report.instalments.map((i) => (
                  <tr key={i.label} className={i.passed ? "" : "text-ink-muted"}>
                    <td className="py-1.5 pr-2">{dayLabel(i.due)}</td>
                    <td className="py-1.5 pr-2 text-ink-muted">{i.cumulative_pct}% of tax due</td>
                    <td className="py-1.5 text-right tabular-nums">{i.passed ? signedMoney(i.net_to_date) : "not yet due"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {(report.charges_from_program > 0 || report.open_positions > 0) && (
            <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-2xs text-warning">
              {report.charges_from_program > 0 && `${report.charges_from_program} trade(s) carry the program's recorded charges, not Angel's — check them against the contract notes. `}
              {report.open_positions > 0 && `${report.open_positions} open position(s) are not included.`}
            </p>
          )}

          <details className="text-2xs text-ink-secondary">
            <summary className="cursor-pointer text-ink-muted">How this is worked out</summary>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {report.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </details>

          <div className="flex flex-wrap items-center gap-2 border-t border-hairline pt-4">
            <span className="text-2xs text-ink-muted">Download FY {report.fy}</span>
            <a href={`/api/proxy/tax.csv?fy=${report.fy}`} className={LINK}>CSV, every trade</a>
            <a href={`/api/proxy/tax.pdf?fy=${report.fy}`} className={LINK}>PDF</a>
          </div>
        </div>
      )}
    </Card>
  );
}
