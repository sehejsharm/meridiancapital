"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { Badge, Card, Empty, Field, Skeleton } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { dayLabel, minutes, money, pnlClass, signedMoney, signedPercent, tradeTime } from "@/lib/format";
import { minuteOfDay, polyline, range, type PathPoint } from "@/lib/tradePath";
import type { TradeDetail } from "@/lib/types";

const W = 800;
const H = 260;

/** One trade on one page: its price path, the fills, what each exit rule set would have done, and the journal around it. */
export default function TradePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [d, setD] = useState<TradeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<TradeDetail>(`/trades/${id}/detail`)
      .then(setD)
      .catch((e) => setError(e instanceof Error ? e.message : "could not load this trade"));
  }, [id]);

  if (error) {
    return (
      <Card title="Trade">
        <Empty>{error === "Not Found" ? "The server does not have the trade page yet. Update the server to switch it on." : error}</Empty>
      </Card>
    );
  }
  if (!d) return <Card title="Trade"><Skeleton rows={8} /></Card>;

  const t = d.trade;
  const net = t.net_angel ?? t.net;
  const gain = t.exit_prem != null && t.entry_prem ? t.exit_prem / t.entry_prem - 1 : null;
  return (
    <div className="space-y-5">
      <p className="text-xs text-ink-muted">
        <Link href="/trades" className="text-brand underline underline-offset-2">Blotter</Link> · {dayLabel(t.session_date)} ·{" "}
        <Link href={`/replay?day=${t.session_date}`} className="text-brand underline underline-offset-2">replay the session</Link>
      </p>

      <Card
        title={t.tsym ?? "Trade"}
        subtitle={`${d.algo_name} · ${t.mode === "live" ? "real money" : "paper"} · ${tradeTime(t.entry_ts)} → ${t.exit_ts ? tradeTime(t.exit_ts) : "open"}`}
        action={net != null ? <Badge tone={net > 0 ? "good" : "critical"}>{net > 0 ? "Win" : "Loss"}</Badge> : <Badge>Open</Badge>}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <div className="text-2xs uppercase tracking-[0.14em] text-ink-muted">Result after charges</div>
            <div className={`mt-1 text-3xl font-semibold tabular-nums ${pnlClass(net)}`}>{signedMoney(net)}</div>
            <div className={`mt-1 text-sm tabular-nums ${pnlClass(gain)}`}>{gain != null ? `${signedPercent(gain)} on premium` : "—"}</div>
          </div>
          <dl className="divide-y divide-hairline">
            <Field label="Bought at" value={money(t.entry_prem, true)} />
            <Field label="Sold at" value={t.exit_prem != null ? money(t.exit_prem, true) : "—"} />
            <Field label="Size" value={`${t.lots ?? "—"} lot · ${t.qty ?? "—"} qty`} />
            <Field label="Best gain" value={t.peak_pct != null ? signedPercent(t.peak_pct / 100, 1) : "—"} />
            <Field label="Held" value={minutes(t.hold_min)} />
            <Field label="Why it was sold" value={t.reason ?? "—"} />
          </dl>
        </div>
      </Card>

      <Card
        title="Price path"
        subtitle={
          d.bars
            ? `${d.bars_source}; the program's own readings are overlaid while it held the trade`
            : d.tick_count
              ? `${d.tick_count} readings the program took while it held the trade`
              : undefined
        }
      >
        <PathChart d={d} />
      </Card>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Card title="What each exit rule set would have done" subtitle="The same entry, replayed minute by minute over this trade's own prices">
          {!d.rules || !Object.keys(d.rules.rules).length ? (
            <Empty>No price path is on record for this trade, so the rule sets cannot be replayed.</Empty>
          ) : (
            <>
              <ul className="divide-y divide-hairline text-xs">
                <li className="flex flex-wrap justify-between gap-2 py-2">
                  <span className="text-ink">As traded</span>
                  <span className="tabular-nums text-ink-secondary">
                    {d.rules.actual.exit_time} · {d.rules.actual.reason ?? "—"} · <span className={pnlClass(d.rules.actual.net)}>{signedMoney(d.rules.actual.net)}</span>
                  </span>
                </li>
                {Object.values(d.rules.rules).map((r) => (
                  <li key={r.rule} className="flex flex-wrap justify-between gap-2 py-2">
                    <span className="min-w-0 break-words text-ink">{r.rule}</span>
                    <span className="tabular-nums text-ink-secondary">
                      {r.decided ? (
                        <>
                          {r.exit_time} · {r.reason} at {money(r.exit, true)} · <span className={pnlClass(r.net)}>{signedMoney(r.net)}</span>
                        </>
                      ) : (
                        "still holding when the recorded path ends"
                      )}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-2xs text-ink-muted">
                {d.rules.path.complete
                  ? "Replayed on the whole day's one-minute prices."
                  : "Replayed on the program's readings, which stop at the real exit."}{" "}
                One lot, after the research charge model; for comparison, not a second set of books.
              </p>
            </>
          )}
        </Card>

        <Card title="Fills" subtitle={d.fills.length ? "Angel's trade book for this contract that day" : undefined}>
          {d.execution && (
            <dl className="mb-3 divide-y divide-hairline">
              <Field
                label="Exit against the last price seen"
                value={d.execution.vs_last_seen_rs != null ? `${signedMoney(d.execution.vs_last_seen_rs)} (saw ${money(d.execution.last_seen, true)} at ${d.execution.last_seen_time})` : "—"}
              />
              <Field
                label="Exit against that minute's close"
                value={d.execution.vs_minute_close_rs != null ? `${signedMoney(d.execution.vs_minute_close_rs)} (closed ${money(d.execution.minute_close, true)})` : "not kept"}
              />
              <Field
                label="Entry against that minute's close"
                value={d.execution.entry_vs_minute_close_rs != null ? signedMoney(d.execution.entry_vs_minute_close_rs) : "not kept"}
              />
            </dl>
          )}
          {d.fills.length ? (
            <ul className="divide-y divide-hairline text-xs">
              {d.fills.map((f, i) => (
                <li key={`${f.order_id}-${i}`} className="flex flex-wrap justify-between gap-2 py-2">
                  <span className="text-ink">{f.side} {f.qty} at {money(f.price, true)}</span>
                  <span className="break-all tabular-nums text-ink-secondary">{f.time} · order {f.order_id}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>
              {d.fills_checked
                ? "Angel's books were not read that day, so there are no order ids to show."
                : "Order ids are kept by the after-close check, from the day the server is updated."}
            </Empty>
          )}
        </Card>
      </div>

      <Card title="Journal around the trade" subtitle="Three minutes before the entry to three minutes after the exit">
        {d.events.length === 0 ? (
          <Empty>No journal lines are on record for this window.</Empty>
        ) : (
          <ol className="max-h-96 space-y-1 overflow-y-auto text-xs">
            {d.events.map((e) => (
              <li key={e.id} className="flex gap-3 border-b border-hairline pb-1 last:border-0">
                <span className="shrink-0 tabular-nums text-ink-muted">{e.ts.slice(11, 19)}</span>
                <span className={`min-w-0 break-words ${e.level === "error" || e.level === "critical" ? "text-critical" : e.level === "warn" ? "text-warning" : "text-ink-secondary"}`}>
                  {e.message}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

function PathChart({ d }: { d: TradeDetail }) {
  const t = d.trade;
  const bars: PathPoint[] = (d.bars ?? []).map((b) => ({ t: minuteOfDay(b[0]), v: b[4] }));
  const ticks: PathPoint[] = d.ticks.map((k) => ({ t: minuteOfDay(k.ts), v: k.premium }));
  const stops: PathPoint[] = d.ticks.filter((k) => k.stop_price).map((k) => ({ t: minuteOfDay(k.ts), v: k.stop_price as number }));
  if (!bars.length && !ticks.length) {
    return <Empty>No price path was recorded for this trade. Paths are kept for trades taken after the server update.</Empty>;
  }
  const entryT = minuteOfDay(t.entry_ts);
  const exitT = t.exit_ts ? minuteOfDay(t.exit_ts.replace(" ", "T")) : null;
  const all = [...bars, ...ticks];
  const t0 = Math.min(...all.map((p) => p.t), entryT);
  const t1 = Math.max(...all.map((p) => p.t), exitT ?? entryT);
  const [lo, hi] = range([...all.map((p) => p.v), ...stops.map((p) => p.v), t.entry_prem ?? NaN, t.exit_prem ?? NaN]);
  const x = (m: number) => ((m - t0) / (t1 - t0 || 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo || 1)) * H;
  const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.round(m % 60)).padStart(2, "0")}`;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-64 w-full" preserveAspectRatio="none" role="img"
        aria-label={`Premium from ${hhmm(t0)} to ${hhmm(t1)}, between ${lo.toFixed(0)} and ${hi.toFixed(0)}`}>
        {t.entry_prem != null && <line x1={0} x2={W} y1={y(t.entry_prem)} y2={y(t.entry_prem)} className="stroke-hairline" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />}
        {bars.length > 1 && <polyline points={polyline(bars, t0, t1, lo, hi, W, H)} fill="none" className="stroke-ink-muted" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
        {ticks.length > 1 && <polyline points={polyline(ticks, t0, t1, lo, hi, W, H)} fill="none" className="stroke-brand" strokeWidth={2} vectorEffect="non-scaling-stroke" />}
        {stops.length > 1 && <polyline points={polyline(stops, t0, t1, lo, hi, W, H)} fill="none" className="stroke-critical" strokeWidth={1.5} strokeDasharray="6 3" vectorEffect="non-scaling-stroke" />}
        <line x1={x(entryT)} x2={x(entryT)} y1={0} y2={H} className="stroke-good" vectorEffect="non-scaling-stroke" />
        {exitT != null && <line x1={x(exitT)} x2={x(exitT)} y1={0} y2={H} className="stroke-critical" vectorEffect="non-scaling-stroke" />}
      </svg>
      <div className="mt-2 flex flex-wrap justify-between gap-x-4 gap-y-1 text-2xs text-ink-muted">
        <span>{hhmm(t0)}</span>
        <span className="flex flex-wrap gap-x-4 gap-y-1">
          <span><span className="text-good">│</span> bought {t.entry_prem != null ? money(t.entry_prem, true) : ""}</span>
          {exitT != null && <span><span className="text-critical">│</span> sold {t.exit_prem != null ? money(t.exit_prem, true) : ""}</span>}
          {ticks.length > 1 && <span><span className="text-brand">━</span> program&apos;s readings</span>}
          {bars.length > 1 && <span>─ whole day, one-minute closes</span>}
          {stops.length > 1 && <span><span className="text-critical">┄</span> stop</span>}
        </span>
        <span>{hhmm(t1)}</span>
      </div>
      <div className="mt-1 flex justify-between text-2xs tabular-nums text-ink-muted">
        <span>low {money(lo, true)}</span>
        <span>high {money(hi, true)}</span>
      </div>
    </div>
  );
}
