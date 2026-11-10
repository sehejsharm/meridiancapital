"use client";

import { useCallback, useEffect, useState } from "react";

import { AlertChannels } from "@/components/AlertChannels";
import { Sparkline } from "@/components/Sparkline";
import { Badge, Button, Card, Empty, StatTile } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { useLiveFeed } from "@/lib/LiveContext";
import { dayLabel } from "@/lib/format";
import { recordedAt } from "@/lib/recorded";
import { API_KINDS, trendText, uptimeTone } from "@/lib/reliability";
import type { ReliabilityReport } from "@/lib/types";
import { pollWhileVisible } from "@/lib/pollWhileVisible";

const RANGES = [7, 30, 90] as const;

/**
 * How reliably the desk ran. Crash loops, Angel's API trouble, the price feed
 * disagreeing with Angel's candles, and engine uptime were all in the database
 * and none of it was on screen; "241 errors logged" in a report's drawdown tile
 * was the only hint.
 */
export default function ReliabilityPage() {
  const { refresh } = useLiveFeed();
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const [data, setData] = useState<ReliabilityReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiGet<ReliabilityReport>(`/reliability?days=${days}`));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load the reliability report");
    }
  }, [days]);

  useEffect(() => pollWhileVisible(() => void load(), 60_000), [load]);

  const ack = async (key: string) => {
    await apiPost("/reliability/ack", { key });
    refresh();
    void load();
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-ink-muted">
          {data?.algo_name ? `Uptime is for ${data.algo_name}, the algorithm trading real money. ` : ""}
          Errors and crashes cover every algorithm.
        </p>
        <div className="flex rounded-md border border-hairline p-0.5 text-2xs" role="tablist" aria-label="Period">
          {RANGES.map((d) => (
            <button
              key={d}
              type="button"
              role="tab"
              aria-selected={days === d}
              onClick={() => setDays(d)}
              className={`rounded px-2.5 py-1 uppercase tracking-[0.1em] touch:min-h-[36px] ${
                days === d ? "bg-brand-dim text-brand" : "text-ink-muted hover:text-ink"
              }`}
            >
              {d} days
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">
          {error}
        </div>
      )}

      {!data ? (
        <ReliabilitySkeleton />
      ) : (
        <>
          {data.alerts.map((a) => (
            <div key={a.key} role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-critical/60 bg-critical/15 px-4 py-3 text-xs text-critical">
              <strong className="font-semibold">Crash loop:</strong>
              <span>
                {a.name ?? a.algo_id} exited abnormally {a.exits} times, {recordedAt(a.first_exit)}–{recordedAt(a.last_exit)} IST.
              </span>
              <Button variant="ghost" onClick={() => void ack(a.key)}>Acknowledge</Button>
            </div>
          ))}

          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <TileWithSpark
              label="Engine uptime, market hours"
              value={data.uptime.overall_pct != null ? `${data.uptime.overall_pct.toFixed(1)}%` : "—"}
              hint={`${data.uptime.daily.length} trading day${data.uptime.daily.length === 1 ? "" : "s"} with a run`}
              spark={data.uptime.daily.map((d) => d.pct)}
              tone={uptimeTone(data.uptime.overall_pct)}
            />
            <TileWithSpark
              label="Angel API errors"
              value={String(data.api.totals.total)}
              hint={trendText(data.api.trend)}
              spark={data.api.daily.map((d) => d.total)}
              tone={data.api.totals.total ? "warning" : "good"}
            />
            <TileWithSpark
              label="Rate-limit incidents"
              value={String(data.api.totals.rate_limits)}
              hint={`${data.api.totals.timeouts} timeouts · ${data.api.totals.upstream} upstream errors`}
              spark={data.api.daily.map((d) => d.rate_limits)}
              tone={data.api.totals.rate_limits ? "warning" : "good"}
            />
            <TileWithSpark
              label="Crash loops / abnormal exits"
              value={`${data.runs.loops.length} / ${data.runs.abnormal_exits}`}
              hint={`${data.runs.total_runs} runs · a loop is ${data.thresholds.loop_threshold}+ exits ≤${data.thresholds.loop_gap_min} min apart`}
              spark={[]}
              tone={data.runs.loops.length ? "critical" : data.runs.abnormal_exits ? "warning" : "good"}
            />
          </div>

          <Card title="Angel API errors per day" subtitle="Read timeouts, rate limits and upstream errors the engines logged, by kind">
            <ApiBars report={data} />
          </Card>

          <div className="grid gap-5 xl:grid-cols-2">
            <Card
              title="Price feed vs Angel's candles"
              subtitle={
                data.divergence.count
                  ? `${data.divergence.count} time${data.divergence.count === 1 ? "" : "s"} the live stream and Angel's candles disagreed; Angel's were used each time`
                  : "The live stream and Angel's candles agreed throughout"
              }
              action={data.divergence.max_pts != null ? <Badge tone="warning">max {data.divergence.max_pts.toFixed(1)} pts</Badge> : undefined}
            >
              {data.divergence.count === 0 ? (
                <Empty>No divergence logged in this period.</Empty>
              ) : (
                <>
                  <Sparkline values={data.divergence.events.map((e) => e.pts)} height={48} className="text-warning" label="Divergence size, oldest to newest" />
                  <ol className="mt-3 max-h-56 space-y-1 overflow-y-auto break-words text-xs">
                    {[...data.divergence.events].reverse().map((e, i) => (
                      <li key={`${e.ts}-${i}`} className="flex justify-between gap-3 border-b border-hairline pb-1 last:border-0">
                        <span className="tabular-nums text-ink-secondary">{recordedAt(e.ts)} IST</span>
                        <span className={`tabular-nums ${e.pts >= 100 ? "text-critical" : "text-warning"}`}>{e.pts.toFixed(1)} pts</span>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </Card>

            <Card title="Crashes" subtitle="Exits the engine was not asked for, newest first">
              {!data.runs.loops.length && !data.runs.crashes.length ? (
                <Empty>No abnormal exits in this period.</Empty>
              ) : (
                <ol className="max-h-72 space-y-2 overflow-y-auto break-words text-xs">
                  {[...data.runs.loops.map((e) => ({ ...e, loop: true })), ...data.runs.crashes.map((e) => ({ ...e, loop: false }))]
                    .sort((a, b) => b.last_exit.localeCompare(a.last_exit))
                    .map((e) => (
                      <li key={e.key} className="flex flex-wrap items-baseline justify-between gap-2 border-b border-hairline pb-2 last:border-0">
                        <span className="text-ink">
                          {e.loop && <span className="mr-2"><Badge tone="critical">loop</Badge></span>}
                          {e.name ?? e.algo_id}
                          <span className="ml-2 text-2xs text-ink-muted">
                            {e.exits > 1 ? `${e.exits} exits, ${recordedAt(e.first_exit)}–${recordedAt(e.last_exit)}` : recordedAt(e.last_exit)} IST
                          </span>
                        </span>
                        <span className="text-2xs tabular-nums text-critical">
                          exit {[...new Set(e.codes)].join(", ")}{e.mode === "live" ? " · real money" : ""}
                        </span>
                      </li>
                    ))}
                </ol>
              )}
            </Card>
          </div>

          <AlertChannels />

          <Card title="Uptime by trading day" subtitle="Share of 09:15–15:30 a process was running, for each day it ran">
            {!data.uptime.daily.length ? (
              <Empty>No runs in this period.</Empty>
            ) : (
              <div className="flex h-28 items-end gap-1" role="img" aria-label="Uptime per trading day">
                {data.uptime.daily.map((d) => (
                  <div key={d.date} className="group relative flex h-full min-w-0 flex-1 items-end" title={`${dayLabel(d.date)}: ${d.pct}% · ${d.down_min} min down`}>
                    <div
                      className={`w-full rounded-t-sm ${{ good: "bg-good/70", warning: "bg-warning/80", critical: "bg-critical/80", neutral: "bg-series" }[uptimeTone(d.pct)]}`}
                      style={{ height: `${Math.max(2, d.pct)}%` }}
                    />
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function TileWithSpark({
  label, value, hint, spark, tone,
}: { label: string; value: string; hint: string; spark: number[]; tone: "good" | "warning" | "critical" | "neutral" }) {
  const color = { good: "text-good", warning: "text-warning", critical: "text-critical", neutral: "text-brand" }[tone];
  return (
    <div className="min-w-0 rounded-lg border border-hairline bg-surface px-3 py-3 sm:px-4">
      <div className="truncate text-2xs uppercase tracking-[0.14em] text-ink-muted">{label}</div>
      <div className={`mt-1.5 truncate text-xl font-semibold tabular-nums sm:text-2xl ${color}`}>{value}</div>
      <div className="mt-1.5"><Sparkline values={spark} className={color} /></div>
      <div className="mt-1 text-2xs text-ink-muted">{hint}</div>
    </div>
  );
}

function ApiBars({ report }: { report: ReliabilityReport }) {
  const max = Math.max(1, ...report.api.daily.map((d) => d.total));
  return (
    <div>
      <div className="flex h-40 items-end gap-[3px]" role="img" aria-label="Angel API errors per day, stacked by kind">
        {report.api.daily.map((d) => (
          <div key={d.date} className="flex h-full min-w-0 flex-1 flex-col-reverse" title={`${dayLabel(d.date)}: ${d.timeouts} timeouts, ${d.rate_limits} rate limits, ${d.upstream} upstream, ${d.other_errors} other`}>
            {API_KINDS.map((k) => {
              const n = d[k.key];
              return n ? <div key={k.key} className={`${k.className} w-full opacity-80`} style={{ height: `${(100 * n) / max}%` }} /> : null;
            })}
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap justify-between gap-2 text-2xs text-ink-muted">
        <span>{dayLabel(report.api.daily[0]?.date)}</span>
        <span className="flex flex-wrap gap-3">
          {API_KINDS.map((k) => (
            <span key={k.key} className="inline-flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-sm ${k.className}`} aria-hidden="true" />
              {k.label} {report.api.totals[k.key]}
            </span>
          ))}
        </span>
        <span>{dayLabel(report.api.daily[report.api.daily.length - 1]?.date)}</span>
      </div>
    </div>
  );
}

/** Neutral placeholders while the report loads — never a confident zero. */
function ReliabilitySkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading the reliability report">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-lg border border-hairline bg-surface" />
        ))}
      </div>
      <div className="h-52 animate-pulse rounded-lg border border-hairline bg-surface" />
    </div>
  );
}
