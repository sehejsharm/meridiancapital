"use client";

import { useCallback, useEffect, useState } from "react";

import { EmergencyStop } from "@/components/EmergencyStop";
import { Badge, Button, Card, Empty, Field } from "@/components/ui";
import { apiDelete, apiGet, apiPost } from "@/lib/client-api";
import { istDateTime } from "@/lib/format";
import { useLiveFeed } from "@/lib/LiveContext";
import type { Holiday } from "@/lib/types";

type Notice = { tone: "good" | "critical"; text: string } | null;

export default function ControlsPage() {
  const { snapshot, status, refresh } = useLiveFeed();
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const run = useCallback(
    async (key: string, action: () => Promise<{ detail?: string }>, success: string) => {
      setBusy(key);
      setNotice(null);
      try {
        const result = await action();
        setNotice({ tone: "good", text: result.detail || success });
        refresh();
      } catch (e) {
        setNotice({ tone: "critical", text: e instanceof Error ? e.message : "request failed" });
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  const running = status?.engine.running ?? false;
  const mode = status?.engine.mode ?? "paper";
  const schedule = status?.schedule;
  const hasPosition = Boolean(snapshot?.position);

  return (
    <div className="space-y-5">
      {notice && (
        <div
          role="status"
          className={`rounded-lg border px-4 py-3 text-xs ${
            notice.tone === "good"
              ? "border-good/40 bg-good/10 text-good"
              : "border-critical/40 bg-critical/10 text-critical"
          }`}
        >
          {notice.text}
        </div>
      )}

      <EmergencyStop onDone={refresh} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Card
          title="Engine"
          subtitle="The trading process itself"
          action={<Badge tone={running ? "good" : "warning"}>{running ? "Running" : "Stopped"}</Badge>}
        >
          <dl className="divide-y divide-hairline">
            <Field label="Mode" value={mode === "live" ? "LIVE — real orders" : "Paper — no orders"} />
            <Field label="Process id" value={status?.engine.pid ?? "—"} />
            <Field
              label="Stopped by operator"
              value={schedule?.manual_override ? "Yes — automation will not restart it" : "No"}
            />
            <Field label="Restarts this session" value={status?.engine.restarts_this_session ?? 0} />
            {status?.engine.last_stop_reason && (
              <Field label="Last stop" value={status.engine.last_stop_reason} mono={false} />
            )}
          </dl>

          {status?.engine.last_output?.length ? (
            <div className="mt-4">
              <p className="text-2xs uppercase tracking-wide text-ink-muted">
                What the engine said before it died
              </p>
              <pre className="mt-1.5 max-h-40 overflow-auto rounded-lg bg-surface-raised p-2.5 text-2xs leading-relaxed text-warning">
                {status.engine.last_output.join("\n")}
              </pre>
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={running || busy !== null}
              onClick={() =>
                void run("start", () => apiPost("/control/engine/start"), "engine started")
              }
            >
              {busy === "start" ? "Starting…" : "Start"}
            </Button>
            <Button
              disabled={!running || busy !== null}
              onClick={() =>
                void run(
                  "stop",
                  () => apiPost("/control/engine/stop", { reason: "operator stop" }),
                  "engine stopped",
                )
              }
            >
              {busy === "stop" ? "Stopping…" : "Stop"}
            </Button>
            <Button
              disabled={!running || busy !== null}
              onClick={() =>
                void run("restart", () => apiPost("/control/engine/restart"), "engine restarted")
              }
            >
              Restart
            </Button>
          </div>
          {hasPosition && (
            <p className="mt-3 text-2xs text-warning">
              A position is open. Stopping is refused until it is flat — flatten it first, or square
              off in the Angel One app.
            </p>
          )}
        </Card>

        <ModeCard running={running} mode={mode} busy={busy} onRun={run} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card
          title="Automation"
          subtitle="Starts at 09:05 and stops at 15:25 IST on NSE trading days"
          action={
            <Badge tone={schedule?.enabled ? "good" : "warning"}>
              {schedule?.enabled ? "Armed" : "Disarmed"}
            </Badge>
          }
        >
          <dl className="divide-y divide-hairline">
            <Field label="Today is a trading day" value={schedule?.is_trading_day ? "Yes" : "No"} />
            <Field label="Inside session window" value={schedule?.in_session_window ? "Yes" : "No"} />
            <Field
              label="Next transition"
              value={
                schedule?.next.at
                  ? `${schedule.next.action} at ${istDateTime(schedule.next.at)}`
                  : "nothing scheduled"
              }
            />
            <Field label="Last check" value={istDateTime(schedule?.last_tick)} />
            <Field label="Decision" value={schedule?.last_decision ?? "—"} mono={false} />
          </dl>

          <div className="mt-4 flex gap-2">
            <Button
              variant={schedule?.enabled ? "default" : "primary"}
              disabled={busy !== null}
              onClick={() =>
                void run(
                  "schedule",
                  () => apiPost("/control/schedule", { enabled: !schedule?.enabled }),
                  schedule?.enabled ? "automation disarmed" : "automation armed",
                )
              }
            >
              {schedule?.enabled ? "Disarm automation" : "Arm automation"}
            </Button>
          </div>

          {schedule && !schedule.calendar_configured && (
            <p className="mt-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-2xs text-warning">
              No NSE holidays are loaded for this year. The engine will boot on exchange holidays
              and idle — its staleness guards refuse to trade a dead feed, so this costs a broker
              session rather than risking a trade. Add the dates below to stop it.
            </p>
          )}
        </Card>

        <Card
          title="In-session risk"
          subtitle="Takes effect on the engine's next loop, within seconds"
        >
          <dl className="divide-y divide-hairline">
            <Field label="Entries halted" value={snapshot?.guards.halted ? "Yes" : "No"} />
            <Field label="Weekly kill active" value={snapshot?.guards.week_halted ? "Yes" : "No"} />
            <Field label="Open position" value={snapshot?.position?.tsym ?? "flat"} />
          </dl>

          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              disabled={!running || busy !== null}
              onClick={() =>
                void run("halt", () => apiPost("/control/halt"), "no new entries will be taken")
              }
            >
              Halt new entries
            </Button>
            <Button
              disabled={!running || busy !== null}
              onClick={() => void run("resume", () => apiPost("/control/resume"), "entries re-armed")}
            >
              Resume
            </Button>
            <Button
              disabled={!running || busy !== null}
              onClick={() =>
                void run(
                  "resume-week",
                  () => apiPost("/control/resume", { week: true }),
                  "weekly kill cleared",
                )
              }
            >
              Clear weekly kill
            </Button>
          </div>

          <FlattenControl running={running} hasPosition={hasPosition} busy={busy} onRun={run} />
        </Card>
      </div>

      <HolidayCalendar onNotice={setNotice} />
    </div>
  );
}

function ModeCard({
  running,
  mode,
  busy,
  onRun,
}: {
  running: boolean;
  mode: string;
  busy: string | null;
  onRun: (k: string, a: () => Promise<{ detail?: string }>, s: string) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <Card
      title="Trading mode"
      subtitle="Paper places no orders; live sends real orders to Angel One"
      action={<Badge tone={mode === "live" ? "critical" : "neutral"}>{mode}</Badge>}
    >
      {running ? (
        <Empty>
          Stop the engine to change mode. A running engine holds its mode for the whole session, so
          it can never flip from paper to live with a position open.
        </Empty>
      ) : mode === "live" ? (
        <div className="space-y-3">
          <p className="text-xs text-ink-secondary">
            The engine is armed for real orders on your Angel One account.
          </p>
          <Button
            disabled={busy !== null}
            onClick={() =>
              void onRun(
                "mode",
                () => apiPost("/control/mode", { mode: "paper" }),
                "back to paper trading",
              )
            }
          >
            Switch to paper
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-ink-secondary">
            Going live means real money on every fill.
          </p>
          <Button
            variant="danger"
            disabled={busy !== null}
            onClick={() => {
              if (!confirming) {
                setConfirming(true);
                return;
              }
              void onRun(
                "mode",
                () => apiPost("/control/mode", { mode: "live", confirm: true }),
                "engine armed for LIVE trading",
              ).then(() => setConfirming(false));
            }}
          >
            {confirming ? "Tap again to arm real money" : "Arm live trading"}
          </Button>
          {confirming && (
            <Button variant="ghost" disabled={busy !== null} onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}

function FlattenControl({
  running,
  hasPosition,
  busy,
  onRun,
}: {
  running: boolean;
  hasPosition: boolean;
  busy: string | null;
  onRun: (k: string, a: () => Promise<{ detail?: string }>, s: string) => Promise<void>;
}) {
  const [confirm, setConfirm] = useState("");

  return (
    <div className="mt-5 rounded-md border border-critical/40 bg-critical/5 p-3">
      <div className="text-2xs font-semibold uppercase tracking-[0.14em] text-critical">
        Emergency flatten
      </div>
      <p className="mt-1 text-2xs text-ink-secondary">
        Sends a market exit for the open position immediately and halts further entries. Type{" "}
        <code className="rounded bg-surface-raised px-1 py-0.5 text-critical">FLATTEN</code> to
        confirm.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <input
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder="FLATTEN"
          aria-label="Type FLATTEN to confirm"
          disabled={!running || !hasPosition}
          className="min-w-0 flex-1 rounded-md border border-hairline bg-surface-raised px-3 py-2 text-sm tracking-[0.1em] text-ink outline-none placeholder:text-ink-muted focus:border-critical disabled:opacity-40"
        />
        <Button
          variant="danger"
          disabled={confirm !== "FLATTEN" || !running || !hasPosition || busy !== null}
          onClick={() =>
            void onRun(
              "flatten",
              () => apiPost("/control/flatten", { confirm }),
              "exit order queued",
            ).then(() => setConfirm(""))
          }
        >
          {busy === "flatten" ? "Sending…" : "Flatten now"}
        </Button>
      </div>
      {!hasPosition && (
        <p className="mt-2 text-2xs text-ink-muted">Nothing to flatten — the book is empty.</p>
      )}
    </div>
  );
}

function HolidayCalendar({ onNotice }: { onNotice: (n: Notice) => void }) {
  const [holidays, setHolidays] = useState<Holiday[]>([]);
  const [day, setDay] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await apiGet<{ holidays: Holiday[] }>("/control/holidays");
      setHolidays(data.holidays);
    } catch {
      /* the page already reports connection problems */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    if (!day) return;
    setBusy(true);
    try {
      const data = await apiPost<{ holidays: Holiday[] }>("/control/holidays", { day, label });
      setHolidays(data.holidays);
      setDay("");
      setLabel("");
      onNotice({ tone: "good", text: `${day} added to the holiday calendar` });
    } catch (e) {
      onNotice({ tone: "critical", text: e instanceof Error ? e.message : "could not add" });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (d: string) => {
    try {
      const data = await apiDelete<{ holidays: Holiday[] }>(`/control/holidays/${d}`);
      setHolidays(data.holidays);
    } catch (e) {
      onNotice({ tone: "critical", text: e instanceof Error ? e.message : "could not remove" });
    }
  };

  return (
    <Card
      title="NSE holiday calendar"
      subtitle="Days the scheduler keeps the engine down. Copy them from the NSE circular each year."
      action={<Badge tone="neutral">{holidays.length} dates</Badge>}
    >
      <div className="flex flex-wrap items-end gap-2">
        <label className="w-full min-w-0 text-2xs uppercase tracking-[0.12em] text-ink-muted sm:w-auto sm:flex-1">
          Date
          <input
            type="date"
            value={day}
            onChange={(e) => setDay(e.target.value)}
            className="mt-1 w-full rounded-md border border-hairline bg-surface-raised px-3 py-2 text-sm text-ink outline-none focus:border-brand"
          />
        </label>
        <label className="w-full min-w-0 text-2xs uppercase tracking-[0.12em] text-ink-muted sm:w-auto sm:flex-[2]">
          Label
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Diwali Laxmi Pujan"
            className="mt-1 w-full rounded-md border border-hairline bg-surface-raised px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-muted focus:border-brand"
          />
        </label>
        <Button variant="primary" onClick={() => void add()} disabled={!day || busy}>
          Add
        </Button>
      </div>

      {holidays.length === 0 ? (
        <div className="mt-4">
          <Empty>No holidays loaded.</Empty>
        </div>
      ) : (
        <ul className="mt-4 grid gap-1.5 sm:grid-cols-2">
          {holidays.map((h) => (
            <li
              key={h.day}
              className="flex items-center justify-between gap-3 rounded-md border border-hairline px-3 py-2"
            >
              <span className="min-w-0 text-xs">
                <span className="tabular-nums text-ink">{h.day}</span>
                {h.label && <span className="ml-2 text-ink-muted">{h.label}</span>}
              </span>
              <button
                type="button"
                onClick={() => void remove(h.day)}
                aria-label={`Remove ${h.day}`}
                className="shrink-0 text-2xs uppercase tracking-[0.1em] text-ink-muted transition-colors hover:text-critical"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
