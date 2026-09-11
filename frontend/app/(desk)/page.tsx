"use client";

import { useEffect, useState } from "react";

import { EquityChart, type ChartPoint } from "@/components/EquityChart";
import { EventFeed } from "@/components/EventFeed";
import { GuardRails } from "@/components/GuardRails";
import { PositionPanel } from "@/components/PositionPanel";
import { SignalPanel } from "@/components/SignalPanel";
import { Badge, Card, Empty, Field, StatTile } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import {
  duration,
  istDateTime,
  istTime,
  money,
  percent,
  signedMoney,
  signedPercent,
} from "@/lib/format";
import { useLiveFeed } from "@/lib/LiveContext";
import type { EquityPoint } from "@/lib/types";

export default function DeskPage() {
  const { snapshot, status, events, connection } = useLiveFeed();
  const [curve, setCurve] = useState<EquityPoint[]>([]);

  const sessionDate = snapshot?.market.session_date;
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await apiGet<{ intraday: EquityPoint[] }>("/equity");
        if (!cancelled) setCurve(data.intraday);
      } catch {
        /* the banner already reports a degraded connection */
      }
    };
    void load();
    const timer = setInterval(() => void load(), 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionDate]);

  if (!snapshot) {
    return <EngineDown status={status} connection={connection} events={events} />;
  }

  const { account, guards, engine, market, health } = snapshot;
  const chartPoints: ChartPoint[] = curve.map((p) => ({
    label: p.ts,
    value: p.equity,
    secondary: p.day_pl,
  }));

  return (
    <div className="space-y-5">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Equity (Angel One)"
          value={money(account.equity)}
          delta={{
            text: `${signedMoney(account.day_pl)} · ${signedPercent(account.day_pl_pct)} today`,
            value: account.day_pl,
          }}
          hint={`Opened at ${money(account.start_equity)}`}
          tone="brand"
        />
        <StatTile
          label="Realised today"
          value={signedMoney(account.realised_today)}
          delta={{
            text: `${signedMoney(account.realised_week)} this week`,
            value: account.realised_week,
          }}
          hint={`${guards.trades_today} of ${guards.max_trades_day} trades taken`}
        />
        <StatTile
          label="Drawdown from peak"
          value={percent(account.drawdown_pct)}
          delta={{
            text: `Halt at −${percent(guards.drawdown_stop, 0)}`,
            value: account.drawdown_pct,
          }}
          hint={`Peak ${money(account.peak_equity)}`}
        />
        <StatTile
          label="Session"
          value={market.open ? "Market open" : "Market closed"}
          hint={`${market.session_date} · ${engine.phase.toLowerCase()}`}
        />
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <Card
            title="Equity curve"
            subtitle={`Intraday, sampled every minute · ${market.session_date}`}
            action={
              <Badge tone={account.day_pl >= 0 ? "good" : "critical"}>
                {account.day_pl >= 0 ? "Up" : "Down"} {signedMoney(account.day_pl)}
              </Badge>
            }
          >
            <EquityChart
              points={chartPoints}
              baseline={account.start_equity || null}
              emptyMessage="The curve fills in once the engine has been up for a few minutes."
              xLabel={(p) => istTime(p.label)}
            />
          </Card>

          <PositionPanel position={snapshot.position} />
          <EventFeed events={events} limit={25} title="Recent activity" />
        </div>

        <div className="space-y-5">
          <SignalPanel snapshot={snapshot} />
          <GuardRails guards={guards} drawdownPct={account.drawdown_pct} />

          <Card title="System health">
            <dl className="divide-y divide-hairline">
              <Field
                label="Broker session"
                value={health.broker_client_id ?? "—"}
                tone={health.broker_connected ? "text-good" : "text-critical"}
              />
              <Field
                label="Clock drift"
                value={
                  health.clock_drift_sec != null
                    ? `${health.clock_drift_sec >= 0 ? "+" : "−"}${Math.abs(health.clock_drift_sec).toFixed(2)}s`
                    : "unverified"
                }
                tone={
                  health.clock_drift_sec != null && Math.abs(health.clock_drift_sec) > 5
                    ? "text-warning"
                    : "text-ink"
                }
              />
              <Field label="Engine uptime" value={duration(engine.uptime_sec)} />
              <Field label="Angel API calls" value={String(health.api?.total_calls ?? 0)} />
              <Field
                label="Rate-limit waits"
                value={`${health.api?.throttles ?? 0} · ${(health.api?.waited_sec ?? 0).toFixed(1)}s`}
                tone={(health.api?.throttles ?? 0) > 0 ? "text-warning" : "text-ink"}
              />
              <Field label="Contracts loaded" value={health.contracts_loaded.toLocaleString()} />
              <Field
                label="Next scheduled"
                value={
                  status?.schedule.next.at
                    ? `${status.schedule.next.action} ${istDateTime(status.schedule.next.at)}`
                    : "—"
                }
              />
            </dl>
            {health.last_error && (
              <p className="mt-3 rounded-md border border-critical/40 bg-critical/10 px-3 py-2 text-2xs text-critical">
                Last broker error: {health.last_error}
              </p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function EngineDown({
  status,
  connection,
  events,
}: {
  status: ReturnType<typeof useLiveFeed>["status"];
  connection: string;
  events: ReturnType<typeof useLiveFeed>["events"];
}) {
  const schedule = status?.schedule;
  const offline = status?.offline_note;

  return (
    <div className="space-y-5">
      <Card
        title="Engine offline"
        subtitle="No live snapshot is being published"
        action={<Badge tone={connection === "offline" ? "critical" : "warning"}>{connection}</Badge>}
      >
        {connection === "offline" ? (
          <Empty>
            The dashboard cannot reach the control plane on Oracle Cloud. Check that the API host
            is up and reachable.
          </Empty>
        ) : (
          <dl className="divide-y divide-hairline">
            <Field label="Automation" value={schedule?.enabled ? "Armed" : "Disarmed"} />
            <Field label="Trading day" value={schedule?.is_trading_day ? "Yes" : "No"} />
            <Field
              label="Next scheduled"
              value={
                schedule?.next.at
                  ? `${schedule.next.action} at ${istDateTime(schedule.next.at)}`
                  : "nothing scheduled"
              }
            />
            <Field label="Scheduler decision" value={schedule?.last_decision ?? "—"} mono={false} />
            {offline && <Field label="Last shutdown" value={offline.reason} mono={false} />}
          </dl>
        )}
        <p className="mt-3 text-2xs text-ink-muted">
          The engine is started and stopped automatically around the NSE session. Use Controls to
          start it by hand.
        </p>
      </Card>

      <EventFeed events={events} limit={40} />
    </div>
  );
}
