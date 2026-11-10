"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge, Button, Card, Empty, Skeleton, StatTile } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { dayLabel, pnlClass, signedMoney } from "@/lib/format";
import { barMinute, clock, minuteOf, openAt, SESSION_MINUTES, valueAt } from "@/lib/replay";
import type { TradeRow } from "@/lib/types";

interface ReplaySession {
  algo_id: string | null;
  name?: string;
  day: string | null;
  sessions: string[];
  samples: { ts: string; equity: number; day_pl: number | null; spot: number | null }[];
  trades: TradeRow[];
  events: { id: number; ts: string; level: string; source: string; message: string }[];
  bars: { t: number; o: number; h: number; l: number; c: number }[];
  bars_source?: string | null;
  report?: { net: number; trades: number } | null;
}

/** "CE 24300", else the trading symbol: never "undefined". */
function contract(t: Pick<TradeRow, "side" | "strike" | "tsym">): string {
  if (t.strike != null) return `${t.side ?? ""} ${t.strike}`.trim();
  return t.tsym ?? t.side ?? "—";
}

const SPEEDS = [1, 4, 15] as const; // timeline minutes per tick
const LEVEL = { error: "text-critical", critical: "text-critical", warn: "text-warning", ok: "text-good" } as Record<string, string>;

/**
 * Replay a past session: the index, the day's P&L, every buy and sell, and the
 * journal, on one timeline you can scrub or play. For post-mortems on winning
 * and losing days alike — what the program saw, when, and what it did.
 */
export default function ReplayPage() {
  const [day, setDay] = useState<string | null>(null);
  const [data, setData] = useState<ReplaySession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(SESSION_MINUTES);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(4);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (d: string | null) => {
    setData(null);
    setError(null);
    try {
      const s = await apiGet<ReplaySession>(`/replay${d ? `?day=${d}` : ""}`);
      setData(s);
      setDay(s.day);
      setCursor(SESSION_MINUTES);
      setPlaying(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load the session");
    }
  }, []);

  useEffect(() => {
    // /replay?day=2026-11-06 opens on that session (the Blotter links here).
    const asked = new URLSearchParams(window.location.search).get("day");
    void load(asked && /^\d{4}-\d{2}-\d{2}$/.test(asked) ? asked : null);
  }, [load]);

  useEffect(() => {
    if (!playing) return;
    timer.current = setInterval(() => {
      setCursor((c) => {
        const next = c + speed;
        if (next >= SESSION_MINUTES) {
          setPlaying(false);
          return SESSION_MINUTES;
        }
        return next;
      });
    }, 150);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [playing, speed]);

  const view = useMemo(() => {
    if (!data) return null;
    const pl = data.samples.map((s) => ({ minute: minuteOf(s.ts) ?? -1, value: s.day_pl }));
    const spot = data.samples.map((s) => ({ minute: minuteOf(s.ts) ?? -1, value: s.spot }));
    const bars = data.bars.map((b) => ({ ...b, minute: barMinute(b.t) })).filter((b) => b.minute >= 0 && b.minute <= SESSION_MINUTES);
    const trades = data.trades.map((t) => ({ ...t, entry: minuteOf(t.entry_ts), exit: minuteOf(t.exit_ts) }));
    const events = data.events.map((e) => ({ ...e, minute: minuteOf(e.ts) ?? -1 }));
    return { pl, spot, bars, trades, events };
  }, [data]);

  if (error) return <div role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">{error}</div>;
  if (!data || !view) return <Skeleton rows={8} />;
  if (!data.algo_id) return <Empty>No sessions recorded yet.</Empty>;

  const plNow = valueAt(view.pl, cursor);
  const idx = view.bars.length ? (view.bars.filter((b) => b.minute <= cursor).pop()?.c ?? null) : valueAt(view.spot, cursor);
  const open = openAt(view.trades, cursor);
  const shown = view.events.filter((e) => e.minute <= cursor).slice(-200).reverse();

  return (
    <div className="space-y-5">
      <Card
        title={`Replay · ${data.name ?? data.algo_id}`}
        subtitle={day ? `${dayLabel(day)} · drag the timeline or press play` : undefined}
        action={
          <select
            aria-label="Session"
            value={day ?? ""}
            onChange={(e) => void load(e.target.value)}
            className="rounded-md border border-hairline bg-surface-raised px-2 py-1 text-xs text-ink"
          >
            {data.sessions.map((s) => (
              <option key={s} value={s}>{dayLabel(s)}</option>
            ))}
          </select>
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={() => { if (cursor >= SESSION_MINUTES) setCursor(0); setPlaying((p) => !p); }}>
            {playing ? "Pause" : cursor >= SESSION_MINUTES ? "Play from 09:15" : "Play"}
          </Button>
          <div className="flex rounded-md border border-hairline p-0.5 text-2xs" role="radiogroup" aria-label="Speed">
            {SPEEDS.map((s) => (
              <button key={s} type="button" role="radio" aria-checked={speed === s} onClick={() => setSpeed(s)}
                className={`rounded px-2 py-1 ${speed === s ? "bg-brand-dim text-brand" : "text-ink-muted"}`}>
                {s === 1 ? "1 min" : `${s} min`}/tick
              </button>
            ))}
          </div>
          <span className="font-mono text-sm tabular-nums text-brand">{clock(cursor)} IST</span>
        </div>
        <input
          type="range"
          min={0}
          max={SESSION_MINUTES}
          value={cursor}
          onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)); }}
          aria-label="Time of day"
          className="mt-3 w-full accent-[var(--brand)]"
        />
        <div className="flex justify-between text-2xs text-ink-muted"><span>09:15</span><span>12:00</span><span>15:30</span></div>
      </Card>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatTile label={`Day's P&L at ${clock(cursor)}`} value={signedMoney(plNow)} tone={plNow == null ? "neutral" : plNow >= 0 ? "profit" : "loss"} />
        <StatTile label="NIFTY" value={idx != null ? idx.toLocaleString("en-IN", { maximumFractionDigits: 1 }) : "—"}
          hint={view.bars.length ? `candles: ${data.bars_source ?? "archived"}` : view.spot.some((p) => p.value != null) ? "from the program's status" : "no index recorded for this day"} />
        <StatTile label="Position" value={open.length ? open.map(contract).join(", ") : "flat"} />
        <StatTile label="Session result" value={signedMoney(data.trades.reduce((a, t) => a + Number(t.net_angel ?? t.net ?? 0), 0))}
          hint={`${data.trades.length} trade${data.trades.length === 1 ? "" : "s"}`} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[3fr_2fr]">
        <Card title="The session" subtitle="Index above, day's P&L below; gold triangles are buys and sells, the line is the replay time">
          <ReplayChart view={view} cursor={cursor} />
        </Card>
        <Card title={`Journal to ${clock(cursor)}`} subtitle={`${shown.length} of ${view.events.length} events`}>
          {!shown.length ? (
            <Empty>Nothing logged yet at this time.</Empty>
          ) : (
            <ol className="max-h-[26rem] space-y-1 overflow-y-auto text-2xs">
              {shown.map((e) => (
                <li key={e.id} className={`flex gap-2 border-b border-hairline py-1 last:border-0 ${e.minute === cursor ? "bg-brand-dim" : ""}`}>
                  <span className="w-12 shrink-0 tabular-nums text-ink-muted">{e.ts.slice(11, 16)}</span>
                  <span className={`min-w-0 break-words ${LEVEL[e.level] ?? "text-ink-secondary"}`}>{e.message}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <Card title="Trades" subtitle="Each buy and sell of the session">
        {!data.trades.length ? (
          <Empty>No trade this session.</Empty>
        ) : (
          <ul className="space-y-1 text-xs">
            {view.trades.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-hairline py-1.5 last:border-0">
                <button type="button" className="text-left text-ink hover:text-brand" onClick={() => t.entry != null && setCursor(t.entry)}>
                  {contract(t)} · in {t.entry != null ? clock(t.entry) : "—"} @ {t.entry_prem?.toFixed(2) ?? "—"} · out{" "}
                  {t.exit != null ? clock(t.exit) : "—"} @ {t.exit_prem?.toFixed(2) ?? "—"} · {t.reason ?? ""}
                </button>
                <span className="flex items-center gap-2">
                  {open.some((o) => o.id === t.id) && <Badge tone="brand" dot>open at {clock(cursor)}</Badge>}
                  <span className={`tabular-nums ${pnlClass(Number(t.net_angel ?? t.net ?? 0))}`}>{signedMoney(Number(t.net_angel ?? t.net ?? 0))}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

type View = NonNullable<ReturnType<typeof buildViewType>>;
// Only for the type of the memoised view above.
function buildViewType() {
  return null as unknown as {
    pl: { minute: number; value: number | null }[];
    spot: { minute: number; value: number | null }[];
    bars: { minute: number; o: number; h: number; l: number; c: number }[];
    trades: (TradeRow & { entry: number | null; exit: number | null })[];
    events: { minute: number }[];
  };
}

function ReplayChart({ view, cursor }: { view: View; cursor: number }) {
  const W = 375, H = 120, PH = 50;
  const x = (m: number) => Math.max(0, Math.min(W, m));
  const prices = view.bars.length
    ? view.bars.flatMap((b) => [b.h, b.l])
    : view.spot.map((p) => p.value).filter((v): v is number => v != null);
  const lo = Math.min(...prices), hi = Math.max(...prices);
  const py = (v: number) => (prices.length ? H - 4 - ((v - lo) / (hi - lo || 1)) * (H - 8) : H / 2);
  const pls = view.pl.map((p) => p.value).filter((v): v is number => v != null);
  const plo = Math.min(0, ...pls), phi = Math.max(0, ...pls);
  const qy = (v: number) => PH - 3 - ((v - plo) / (phi - plo || 1)) * (PH - 6);
  const line = (pts: { minute: number; value: number | null }[], y: (v: number) => number) =>
    pts.filter((p) => p.value != null && p.minute >= 0 && p.minute <= cursor)
      .map((p, i) => `${i ? "L" : "M"}${x(p.minute).toFixed(1)},${y(p.value as number).toFixed(1)}`).join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-56 w-full" role="img" aria-label="NIFTY through the session">
        {view.bars.length ? (
          view.bars.map((b) => (
            <g key={b.minute} opacity={b.minute <= cursor ? 1 : 0.15}>
              <line x1={x(b.minute)} x2={x(b.minute)} y1={py(b.h)} y2={py(b.l)} stroke="currentColor"
                className={b.c >= b.o ? "text-profit" : "text-loss"} strokeWidth={0.4} vectorEffect="non-scaling-stroke" />
              <line x1={x(b.minute)} x2={x(b.minute)} y1={py(b.o)} y2={py(b.c)} stroke="currentColor"
                className={b.c >= b.o ? "text-profit" : "text-loss"} strokeWidth={0.9} />
            </g>
          ))
        ) : (
          <path d={line(view.spot, py)} fill="none" stroke="currentColor" className="text-series" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
        )}
        {view.trades.flatMap((t) => [
          t.entry != null && t.entry <= cursor && t.spot_entry != null && prices.length ? (
            <path key={`e${t.id}`} d={`M${x(t.entry) - 3},${py(t.spot_entry) + 5} L${x(t.entry) + 3},${py(t.spot_entry) + 5} L${x(t.entry)},${py(t.spot_entry)} Z`} className="fill-brand" />
          ) : null,
          t.exit != null && t.exit <= cursor && t.spot_exit != null && prices.length ? (
            <path key={`x${t.id}`} d={`M${x(t.exit) - 3},${py(t.spot_exit) - 5} L${x(t.exit) + 3},${py(t.spot_exit) - 5} L${x(t.exit)},${py(t.spot_exit)} Z`} className="fill-brand" />
          ) : null,
        ])}
        <line x1={x(cursor)} x2={x(cursor)} y1={0} y2={H} stroke="currentColor" className="text-brand" strokeDasharray="2 2" strokeWidth={0.8} vectorEffect="non-scaling-stroke" />
      </svg>
      {!prices.length && <p className="text-2xs text-ink-muted">No index was recorded for this day (sessions from 7 Nov 2026 on are).</p>}
      <svg viewBox={`0 0 ${W} ${PH}`} preserveAspectRatio="none" className="mt-2 block h-24 w-full" role="img" aria-label="Day's P&L through the session">
        <line x1={0} x2={W} y1={qy(0)} y2={qy(0)} stroke="currentColor" className="text-hairline" strokeWidth={0.6} vectorEffect="non-scaling-stroke" />
        <path d={line(view.pl, qy)} fill="none" stroke="currentColor" className="text-brand" strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
        <line x1={x(cursor)} x2={x(cursor)} y1={0} y2={PH} stroke="currentColor" className="text-brand" strokeDasharray="2 2" strokeWidth={0.8} vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}
