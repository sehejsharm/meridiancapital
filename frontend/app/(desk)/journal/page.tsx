"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { EventFeed } from "@/components/EventFeed";
import { LogDownload } from "@/components/LogDownload";
import { Badge, Card, Empty, Field } from "@/components/ui";
import { fetchAlgos } from "@/lib/algos";
import { apiGet } from "@/lib/client-api";
import { duration, istDateTime, money, percent, signedMoney } from "@/lib/format";
import { useLiveFeed } from "@/lib/LiveContext";
import type { Algo, CommandRow, EventRow } from "@/lib/types";

/** Where the control plane files events that belong to the desk, not one algorithm. */
const SYSTEM = "system";
const ALL = "all";
const SCOPE_KEY = "meridian:journal-scope";

interface EodReport {
  session_date: string;
  algo_id?: string;
  mode: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  gross: number;
  charges: number;
  net: number;
  best: number;
  worst: number;
  avg_hold_min: number;
  start_equity: number;
  end_equity: number;
  day_pl: number;
  drawdown_pct: number;
}

interface RunRow {
  id: number;
  started_ts: string;
  stopped_ts: string | null;
  pid: number | null;
  mode: string | null;
  trigger: string | null;
  exit_code: number | null;
  reason: string | null;
  algo_id?: string;
}

function readScope(): string {
  try {
    return window.localStorage.getItem(SCOPE_KEY) || ALL;
  } catch {
    return ALL;
  }
}

function saveScope(scope: string) {
  try {
    window.localStorage.setItem(SCOPE_KEY, scope);
  } catch {
    /* a remembered tab is a convenience; losing it costs nothing */
  }
}

export default function JournalPage() {
  const { events: liveEvents } = useLiveFeed();
  const [algos, setAlgos] = useState<Algo[]>([]);
  const [scope, setScopeState] = useState<string>(ALL);
  const [scopedEvents, setScopedEvents] = useState<EventRow[]>([]);
  const [report, setReport] = useState<EodReport | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [commands, setCommands] = useState<CommandRow[]>([]);

  useEffect(() => setScopeState(readScope()), []);
  const setScope = useCallback((next: string) => {
    setScopeState(next);
    saveScope(next);
  }, []);

  useEffect(() => {
    void fetchAlgos()
      .then((list) => setAlgos(list.algos))
      .catch(() => {
        /* the picker still offers All and Desk */
      });
  }, []);

  const names = useMemo(() => {
    const map: Record<string, string> = { [SYSTEM]: "Desk" };
    for (const a of algos) map[a.id] = a.name;
    return map;
  }, [algos]);

  // A scope that no longer exists (the algorithm was removed) falls back to All.
  useEffect(() => {
    if (algos.length && scope !== ALL && scope !== SYSTEM && !names[scope]) setScope(ALL);
  }, [algos, names, scope, setScope]);

  const filter = scope === ALL ? "" : `algo=${encodeURIComponent(scope)}`;
  const isAlgo = scope !== ALL && scope !== SYSTEM;

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const q = (base: string, extra = "") =>
        `${base}?${[extra, filter].filter(Boolean).join("&")}`;
      const [evts, eod, runList, cmdList] = await Promise.allSettled([
        scope === ALL
          ? Promise.resolve({ events: [] as EventRow[] })
          : apiGet<{ events: EventRow[] }>(q("/events", "limit=200")),
        scope === SYSTEM
          ? Promise.resolve({ report: null })
          : apiGet<{ report: EodReport | null }>(q("/reports/eod")),
        apiGet<{ runs: RunRow[] }>(q("/runs")),
        apiGet<{ commands: CommandRow[] }>(q("/commands")),
      ]);
      if (cancelled) return;
      if (evts.status === "fulfilled") setScopedEvents(evts.value.events);
      if (eod.status === "fulfilled") setReport(eod.value.report);
      if (runList.status === "fulfilled") setRuns(runList.value.runs);
      if (cmdList.status === "fulfilled") setCommands(cmdList.value.commands);
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [scope, filter]);

  // The stream carries every algorithm's lines; a scoped view keeps its own and
  // adds whatever arrives live between refreshes.
  const events = useMemo(() => {
    if (scope === ALL) return liveEvents;
    const byId = new Map<number, EventRow>();
    for (const e of scopedEvents) byId.set(e.id, e);
    for (const e of liveEvents) if (e.algo_id === scope) byId.set(e.id, e);
    return [...byId.values()].sort((a, b) => b.id - a.id);
  }, [scope, scopedEvents, liveEvents]);

  const scopeName = scope === ALL ? "All strategies" : (names[scope] ?? scope);

  return (
    <div className="space-y-5">
      <ScopePicker
        scope={scope}
        options={[
          { id: ALL, label: "All" },
          ...algos.map((a) => ({ id: a.id, label: a.name })),
          { id: SYSTEM, label: "Desk" },
        ]}
        onPick={setScope}
      />

      <LogDownload algoId={scope === ALL ? undefined : scope} />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <EventFeed
          events={events}
          limit={200}
          title="Journal"
          subtitle={scope === ALL ? "Every strategy, newest first" : scopeName}
          algoNames={scope === ALL ? names : undefined}
          emptyText={
            scope === SYSTEM
              ? "No desk-wide events at this level."
              : undefined
          }
        />

        <div className="space-y-5">
          {scope !== SYSTEM && (
          <Card
            title="Last end-of-day report"
            subtitle={
              report
                ? `Session ${report.session_date}${
                    !isAlgo && report.algo_id ? ` · ${names[report.algo_id] ?? report.algo_id}` : ""
                  }`
                : undefined
            }
            action={
              report ? (
                <Badge tone={report.mode === "live" ? "critical" : "neutral"}>
                  {report.mode === "live" ? "Real money" : "Paper"}
                </Badge>
              ) : undefined
            }
          >
            {!report ? (
              <Empty>No session has been closed out yet.</Empty>
            ) : (
              <dl className="divide-y divide-hairline">
                <Field
                  label="Trades"
                  value={`${report.trades} · ${report.wins} won, ${report.losses} lost`}
                />
                <Field
                  label="Win rate"
                  value={report.trades ? percent(report.win_rate, 0) : "—"}
                />
                <Field label="Gross" value={signedMoney(report.gross)} />
                <Field label="Charges" value={money(report.charges)} />
                <Field
                  label="Net"
                  value={signedMoney(report.net)}
                  tone={report.net >= 0 ? "text-profit" : "text-loss"}
                />
                <Field label="Start equity" value={money(report.start_equity)} />
                <Field label="End equity" value={money(report.end_equity)} />
                <Field
                  label="Day P&L"
                  value={signedMoney(report.day_pl)}
                  tone={report.day_pl >= 0 ? "text-profit" : "text-loss"}
                />
                <Field label="Drawdown from peak" value={percent(report.drawdown_pct)} />
                <Field label="Average hold" value={duration(report.avg_hold_min * 60)} />
              </dl>
            )}
          </Card>
          )}

          {scope !== SYSTEM && (
          <>
          <Card title="Engine runs" subtitle="Every start and stop, with why">
            {runs.length === 0 ? (
              <Empty>No runs recorded.</Empty>
            ) : (
              <ol className="space-y-2.5">
                {runs.map((run) => (
                  <li key={run.id} className="border-b border-hairline pb-2.5 last:border-0 last:pb-0">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs tabular-nums text-ink">
                        {istDateTime(run.started_ts)}
                      </span>
                      <Badge
                        tone={
                          run.stopped_ts === null
                            ? "good"
                            : run.exit_code === 0 || run.exit_code === null
                              ? "neutral"
                              : "critical"
                        }
                      >
                        {run.stopped_ts === null ? "Running" : `exit ${run.exit_code ?? "?"}`}
                      </Badge>
                    </div>
                    <div className="mt-0.5 text-2xs text-ink-muted">
                      {!isAlgo && run.algo_id ? `${names[run.algo_id] ?? run.algo_id} · ` : ""}
                      {run.mode === "live" ? "real money" : run.mode} · triggered by{" "}
                      {run.trigger ?? "—"} · pid {run.pid ?? "—"}
                      {run.reason ? ` · ${run.reason}` : ""}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Card>

          <Card title="Control commands" subtitle="Instructions sent to the engine">
            {commands.length === 0 ? (
              <Empty>No commands issued.</Empty>
            ) : (
              <ol className="space-y-2">
                {commands.slice(0, 15).map((cmd) => (
                  <li key={cmd.id} className="flex items-baseline justify-between gap-3 text-xs">
                    <span className="text-ink">
                      <span className="font-medium uppercase tracking-[0.08em]">{cmd.action}</span>
                      <span className="ml-2 text-2xs text-ink-muted">
                        {!isAlgo && cmd.algo_id ? `${names[cmd.algo_id] ?? cmd.algo_id} · ` : ""}
                        {istDateTime(cmd.created_ts)} · {cmd.issued_by ?? "—"}
                      </span>
                    </span>
                    <span
                      className={`shrink-0 text-2xs ${
                        cmd.status === "done"
                          ? "text-good"
                          : cmd.status === "failed"
                            ? "text-critical"
                            : "text-ink-muted"
                      }`}
                      title={cmd.result ?? undefined}
                    >
                      {cmd.status}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
          </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Which strategy's journal to read.
 *
 * A row of chips rather than a select: with two or three strategies every
 * option is visible at once, and on a phone the row scrolls sideways inside
 * itself instead of widening the page.
 */
function ScopePicker({
  scope,
  options,
  onPick,
}: {
  scope: string;
  options: { id: string; label: string }[];
  onPick: (id: string) => void;
}) {
  return (
    <nav aria-label="Strategy" className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <div className="flex w-max gap-1 rounded-lg border border-hairline bg-surface p-1">
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            aria-pressed={scope === o.id}
            onClick={() => onPick(o.id)}
            className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors touch:min-h-[40px] ${
              scope === o.id
                ? "bg-brand-dim text-brand"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
