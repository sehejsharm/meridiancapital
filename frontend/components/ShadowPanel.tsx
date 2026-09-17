"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Empty } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { money, pnlClass, signedMoney } from "@/lib/format";
import type { ShadowComparison } from "@/lib/types";

/**
 * Live against its paper twin.
 *
 * Drag is what paper said you would keep minus what the account actually kept:
 * slippage on both legs, brokerage, STT, exchange and SEBI fees, stamp duty and
 * GST. It is the number a backtest cannot give you and the one that decides
 * whether an edge survives a real broker.
 *
 * Sessions where only one side traded are shown but excluded from the totals —
 * the two engines size independently, so a one-sided day would distort the
 * comparison rather than inform it.
 */
export function ShadowPanel({ algoId }: { algoId: string }) {
  const [data, setData] = useState<ShadowComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiGet<ShadowComparison>(`/algos/${algoId}/shadow`));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load the shadow");
    }
  }, [algoId]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = useCallback(
    async (enabled: boolean) => {
      setBusy(true);
      setError(null);
      try {
        await apiPost(`/algos/${algoId}/shadow`, { enabled });
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : "failed");
      } finally {
        setBusy(false);
      }
    },
    [algoId, load],
  );

  const totals = data?.totals;

  return (
    <Card
      title="Shadow mode"
      subtitle="A paper twin running beside the live account, measuring execution drag"
      action={
        data?.configured ? (
          <Badge tone="brand">{data.paired_sessions ?? 0} paired sessions</Badge>
        ) : null
      }
    >
      {error && <p className="mb-3 text-xs text-critical">{error}</p>}

      {!data ? (
        <p className="text-xs text-ink-muted">Loading…</p>
      ) : !data.configured ? (
        <div className="space-y-3">
          <p className="text-xs leading-relaxed text-ink-secondary">
            Creates a second registration of this exact version, pinned to paper and
            never promotable. Both see the same market and take the same decisions;
            only the live one sends orders. The gap between them is your real cost of
            execution.
          </p>
          <p className="text-2xs text-ink-muted">
            A shadow doubles this algorithm&apos;s calls to Angel One. Those calls come
            out of the same account budget, so the engines will pace each other rather
            than breach the cap — watch the rate gauges if the loop feels slow.
          </p>
          <Button variant="primary" disabled={busy} onClick={() => toggle(true)}>
            {busy ? "Creating…" : "Start a shadow"}
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {totals && (data.paired_sessions ?? 0) > 0 ? (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Figure label="Paper says" value={signedMoney(totals.paper_net)} />
                <Figure
                  label="Account kept"
                  value={signedMoney(totals.live_net)}
                  tone={pnlClass(totals.live_net)}
                />
                <Figure
                  label="Execution drag"
                  value={signedMoney(-totals.drag)}
                  tone={pnlClass(-totals.drag)}
                  hint={
                    totals.drag_pct_of_paper !== null
                      ? `${totals.drag_pct_of_paper.toFixed(1)}% of paper`
                      : undefined
                  }
                />
                <Figure
                  label="Per session"
                  value={
                    totals.avg_drag_per_session !== null
                      ? signedMoney(-totals.avg_drag_per_session)
                      : "—"
                  }
                />
              </div>

              <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-hairline pt-3 text-2xs text-ink-muted">
                <span>
                  Real charges <b className="tabular-nums text-ink">{money(totals.charges_paid)}</b>
                </span>
                <span>
                  Slippage (the rest){" "}
                  <b className="tabular-nums text-ink">{money(totals.slippage_est)}</b>
                </span>
              </div>
            </>
          ) : (
            <Empty>
              No session yet where both sides traded. The comparison starts once the
              live run and the shadow have both taken a trade on the same day.
            </Empty>
          )}

          {(data.sessions?.length ?? 0) > 0 && (
            <div className="-mx-4 overflow-x-auto px-4">
              <table className="w-full min-w-[520px] text-xs">
                <thead>
                  <tr className="border-b border-hairline text-left text-2xs uppercase tracking-[0.12em] text-ink-muted">
                    <th className="py-2 pr-3 font-medium">Session</th>
                    <th className="py-2 pr-3 text-right font-medium">Paper</th>
                    <th className="py-2 pr-3 text-right font-medium">Live</th>
                    <th className="py-2 pr-3 text-right font-medium">Drag</th>
                    <th className="py-2 font-medium">Paired</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {data.sessions!.slice(0, 30).map((s) => (
                    <tr key={s.session_date}>
                      <td className="py-2 pr-3 tabular-nums text-ink-secondary">
                        {s.session_date}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums text-ink-secondary">
                        {s.paper.trades ? signedMoney(s.paper.net) : "—"}
                      </td>
                      <td className={`py-2 pr-3 text-right tabular-nums ${pnlClass(s.live.net)}`}>
                        {s.live.trades ? signedMoney(s.live.net) : "—"}
                      </td>
                      <td className={`py-2 pr-3 text-right tabular-nums ${pnlClass(-s.drag)}`}>
                        {s.both_traded ? signedMoney(-s.drag) : "—"}
                      </td>
                      <td className="py-2">
                        {s.both_traded ? (
                          <span className="text-good">yes</span>
                        ) : (
                          <span className="text-ink-muted">one side only</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 border-t border-hairline pt-3">
            <span className="font-mono text-2xs text-ink-muted">{data.shadow_algo_id}</span>
            <Button variant="ghost" disabled={busy} onClick={() => toggle(false)}>
              Remove shadow
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function Figure({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-[0.12em] text-ink-muted">{label}</div>
      <div className={`truncate text-base font-semibold tabular-nums ${tone ?? "text-ink"}`}>
        {value}
      </div>
      {hint && <div className="text-2xs text-ink-muted">{hint}</div>}
    </div>
  );
}
