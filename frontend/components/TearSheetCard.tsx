"use client";

import { useEffect, useState } from "react";

import { Card, Skeleton } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { pnlClass, signedMoney } from "@/lib/format";
import { periodLabel } from "@/lib/periods";
import type { TearSheet } from "@/lib/types";

const LINK =
  "inline-flex items-center rounded-md border border-brand bg-brand px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] text-[color:var(--plane)] transition-colors hover:bg-brand-bright touch:min-h-[44px] touch:px-4";

function todayIst(): string {
  return new Date(Date.now() + 5.5 * 3600_000).toISOString().slice(0, 10);
}

/**
 * A one-page PDF for a week or a month: KPIs, the P&L curve, each session's
 * result and the trades. A preview of its headline numbers shows here first.
 */
export function TearSheetCard({ algoId }: { algoId: string }) {
  const [period, setPeriod] = useState<"week" | "month">("week");
  const [day, setDay] = useState(todayIst());
  const [sheet, setSheet] = useState<TearSheet | null>(null);
  const [error, setError] = useState<string | null>(null);

  const params = new URLSearchParams({ period, date: day });
  if (algoId) params.set("algo_id", algoId);
  const query = params.toString();

  useEffect(() => {
    let live = true;
    setSheet(null);
    setError(null);
    apiGet<TearSheet>(`/tearsheet?${query}`)
      .then((s) => live && setSheet(s))
      .catch((e) => live && setError(e instanceof Error ? e.message : "could not build the tear sheet"));
    return () => {
      live = false;
    };
  }, [query]);

  const s = sheet?.summary;
  return (
    <Card title="Tear sheet" subtitle="A one-page PDF for a week or a month">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div role="radiogroup" aria-label="Period" className="inline-flex rounded-md border border-hairline p-0.5">
            {(["week", "month"] as const).map((p) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={period === p}
                onClick={() => setPeriod(p)}
                className={`rounded px-3 py-1.5 text-xs font-medium capitalize touch:min-h-[40px] ${period === p ? "bg-brand/15 text-brand" : "text-ink-muted hover:text-ink"}`}
              >
                {p}
              </button>
            ))}
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-[0.12em] text-ink-muted">Any day in it</span>
            <input
              type="date"
              value={day}
              onChange={(e) => e.target.value && setDay(e.target.value)}
              className="rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
            />
          </label>
        </div>

        <div className="rounded-md border border-hairline px-3 py-3">
          <div className="text-2xs uppercase tracking-[0.14em] text-ink-muted">{periodLabel(period, day)}</div>
          {error ? (
            <p role="alert" className="mt-2 text-xs text-critical">{error}</p>
          ) : !sheet || !s ? (
            <Skeleton rows={2} className="mt-2" />
          ) : (
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
              <div>
                <dt className="text-ink-muted">Net</dt>
                <dd className={`font-semibold tabular-nums ${pnlClass(s.net_pnl)}`}>{signedMoney(s.net_pnl)}</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Trades</dt>
                <dd className="tabular-nums">{s.trades} · {s.win_rate_pct.toFixed(0)}% won</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Days</dt>
                <dd className="tabular-nums">{sheet.extra.green_days} green · {sheet.extra.red_days} red</dd>
              </div>
              <div>
                <dt className="text-ink-muted">Sharpe</dt>
                <dd className="tabular-nums">
                  {sheet.ratios.sharpe ?? "—"}
                  {sheet.ratios.few_days && sheet.ratios.sharpe != null && <span className="text-ink-muted"> ({sheet.ratios.days} days)</span>}
                </dd>
              </div>
            </dl>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <a href={`/api/proxy/tearsheet.pdf?${query}`} className={LINK}>
            Download PDF
          </a>
          {sheet?.summary.scope && <span className="text-2xs text-ink-muted">{sheet.summary.scope} · {sheet.name}</span>}
        </div>
      </div>
    </Card>
  );
}
