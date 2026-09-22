"use client";

import { useEffect, useState } from "react";

import { EventFeed } from "@/components/EventFeed";
import { LogDownload } from "@/components/LogDownload";
import { Badge, Card, Empty, Field } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { duration, istDateTime, money, percent, signedMoney } from "@/lib/format";
import { useLiveFeed } from "@/lib/LiveContext";
import type { CommandRow } from "@/lib/types";

interface EodReport {
  session_date: string;
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
}

export default function JournalPage() {
  const { events } = useLiveFeed();
  const [report, setReport] = useState<EodReport | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [commands, setCommands] = useState<CommandRow[]>([]);

  useEffect(() => {
    const load = async () => {
      const [eod, runList, cmdList] = await Promise.allSettled([
        apiGet<{ report: EodReport | null }>("/reports/eod"),
        apiGet<{ runs: RunRow[] }>("/runs"),
        apiGet<{ commands: CommandRow[] }>("/commands"),
      ]);
      if (eod.status === "fulfilled") setReport(eod.value.report);
      if (runList.status === "fulfilled") setRuns(runList.value.runs);
      if (cmdList.status === "fulfilled") setCommands(cmdList.value.commands);
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="space-y-5">
      <LogDownload />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <EventFeed events={events} limit={200} title="Engine journal" />

        <div className="space-y-5">
          <Card
            title="Last end-of-day report"
            subtitle={report ? `Session ${report.session_date}` : undefined}
            action={report ? <Badge tone="neutral">{report.mode}</Badge> : undefined}
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
                      {run.mode} · triggered by {run.trigger ?? "—"} · pid {run.pid ?? "—"}
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
        </div>
      </div>
    </div>
  );
}
