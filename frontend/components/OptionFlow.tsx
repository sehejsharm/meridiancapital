"use client";

import { useMemo, useState } from "react";

import { Badge, Card, Empty } from "@/components/ui";
import { flowBars, READING, rupeesCompact, share, type FlowPayload, type FlowStrike } from "@/lib/flow";

const W = 600;
const H = 150;
const STRIKES_SHOWN = 11;

function Swatch({ side }: { side: "ce" | "pe" }) {
  return <span aria-hidden className={`inline-block h-2.5 w-2.5 rounded-sm ${side === "ce" ? "bg-call" : "bg-put"}`} />;
}

/** Where the money went today: premium traded each minute in calls and puts, and who is positioning at each strike. */
export function OptionFlow({ flow, spot, atm, error }: { flow: FlowPayload | null; spot: number | null; atm: number | null; error: string | null }) {
  const [hover, setHover] = useState<number | null>(null);
  const geo = useMemo(() => (flow?.timeline?.length ? flowBars(flow.timeline, W, H) : null), [flow]);

  const strikes = useMemo<FlowStrike[]>(() => {
    const all = (flow?.strikes ?? []).filter((s) => (s.ce?.volume ?? 0) > 0 || (s.pe?.volume ?? 0) > 0);
    if (!all.length) return [];
    const centre = atm ?? spot ?? all[Math.floor(all.length / 2)].strike;
    return [...all].sort((a, b) => Math.abs(a.strike - centre) - Math.abs(b.strike - centre)).slice(0, STRIKES_SHOWN).sort((a, b) => b.strike - a.strike);
  }, [flow, atm, spot]);
  const maxVol = Math.max(1, ...strikes.flatMap((s) => [s.ce?.volume ?? 0, s.pe?.volume ?? 0]));

  const t = flow?.totals;
  const pcr = t?.put_call_ratio ?? null;
  const lean = pcr == null ? null : pcr > 1.15 ? "Puts heavier" : pcr < 0.87 ? "Calls heavier" : "Balanced";
  const shown = hover != null && geo ? geo.bars[hover] : null;

  return (
    <Card
      title="Option flow"
      subtitle={flow?.available ? `Premium traded each minute, ${flow.since} to ${flow.to} IST. Calls above the line, puts below` : "Where the money is going in the chain"}
      action={flow?.available && lean ? <Badge tone="neutral">{lean}</Badge> : undefined}
    >
      {error && !flow ? (
        <Empty>Option flow unavailable: {error}</Empty>
      ) : !flow ? (
        <Empty>Loading…</Empty>
      ) : !flow.available || !geo ? (
        <Empty>{flow.reason ?? "Option flow builds up once the chain has been read for two minutes."}</Empty>
      ) : (
        <div className="space-y-5">
          <dl className="grid grid-cols-3 gap-3 text-xs">
            <div className="min-w-0">
              <dt className="flex items-center gap-1.5 text-ink-muted"><Swatch side="ce" /> Calls traded</dt>
              <dd className="text-base font-semibold tabular-nums text-ink">{rupeesCompact(t?.ce)}</dd>
            </div>
            <div className="min-w-0">
              <dt className="flex items-center gap-1.5 text-ink-muted"><Swatch side="pe" /> Puts traded</dt>
              <dd className="text-base font-semibold tabular-nums text-ink">{rupeesCompact(t?.pe)}</dd>
            </div>
            <div className="min-w-0">
              <dt className="text-ink-muted">Put / call, by premium</dt>
              <dd className="text-base font-semibold tabular-nums text-ink">{pcr == null ? "—" : pcr.toFixed(2)}</dd>
            </div>
          </dl>

          <div>
            <p className="mb-1 min-h-4 text-2xs tabular-nums text-ink-secondary" aria-live="polite">
              {shown ? `${shown.t} · calls ${rupeesCompact(shown.ce)} · puts ${rupeesCompact(shown.pe)}` : `Busiest minute: ${rupeesCompact(geo.max)} of premium. Point at a bar for its minute.`}
            </p>
            <svg
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="none"
              className="block h-40 w-full"
              role="img"
              aria-label={`Premium traded per minute: calls ${rupeesCompact(t?.ce)}, puts ${rupeesCompact(t?.pe)} in total`}
              onMouseLeave={() => setHover(null)}
              onMouseMove={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                const i = Math.floor(((e.clientX - r.left) / r.width) * geo.bars.length);
                setHover(Math.max(0, Math.min(geo.bars.length - 1, i)));
              }}
            >
              <defs>
                <linearGradient id="flow-ce" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--series-call)" stopOpacity="1" />
                  <stop offset="100%" stopColor="var(--series-call)" stopOpacity="0.45" />
                </linearGradient>
                <linearGradient id="flow-pe" x1="0" y1="1" x2="0" y2="0">
                  <stop offset="0%" stopColor="var(--series-put)" stopOpacity="1" />
                  <stop offset="100%" stopColor="var(--series-put)" stopOpacity="0.45" />
                </linearGradient>
              </defs>
              {geo.bars.map((b, i) => (
                <g key={b.t} opacity={hover == null || hover === i ? 1 : 0.35}>
                  <rect x={b.x} y={geo.mid - 1 - b.ceH} width={b.w} height={Math.max(0, b.ceH)} rx={Math.min(2, b.w / 2)} fill="url(#flow-ce)" />
                  <rect x={b.x} y={geo.mid + 1} width={b.w} height={Math.max(0, b.peH)} rx={Math.min(2, b.w / 2)} fill="url(#flow-pe)" />
                </g>
              ))}
              <line x1="0" x2={W} y1={geo.mid} y2={geo.mid} stroke="var(--hairline)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            </svg>
            <div className="mt-1 flex justify-between text-2xs tabular-nums text-ink-muted">
              <span>{flow.since}</span>
              <span className="flex items-center gap-3"><span className="flex items-center gap-1"><Swatch side="ce" /> calls</span><span className="flex items-center gap-1"><Swatch side="pe" /> puts</span></span>
              <span>{flow.to}</span>
            </div>
          </div>

          {strikes.length > 0 && (
            <div>
              <h3 className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-secondary">
                Who is positioning, {flow.window_from} to {flow.to}
              </h3>
              <p className="mt-1 text-2xs text-ink-muted">Bar length: contracts traded. Words: premium and open interest read together.</p>
              <ul className="mt-3 space-y-1.5">
                {strikes.map((s) => {
                  const isAtm = s.strike === atm;
                  return (
                    <li key={s.strike} className="grid grid-cols-[minmax(0,1fr)_4.5rem_minmax(0,1fr)] items-center gap-2 text-2xs">
                      <div className="flex min-w-0 flex-col items-end">
                        <div className="flex h-2.5 w-full justify-end">
                          <span className="h-full rounded-l-sm bg-put shadow-[0_0_10px_var(--series-put)] transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${share(s.pe?.volume, maxVol)}%` }} />
                        </div>
                        <span className="mt-0.5 truncate text-ink-muted" title={s.pe ? READING[s.pe.reading].long : undefined}>
                          {s.pe ? `${READING[s.pe.reading].short} · OI ${signed(s.pe.oi_change)}` : "—"}
                        </span>
                      </div>
                      <span className={`text-center font-semibold tabular-nums ${isAtm ? "rounded border border-brand/50 bg-brand-dim text-brand" : "text-ink"}`}>{s.strike}</span>
                      <div className="flex min-w-0 flex-col items-start">
                        <div className="flex h-2.5 w-full justify-start">
                          <span className="h-full rounded-r-sm bg-call shadow-[0_0_10px_var(--series-call)] transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${share(s.ce?.volume, maxVol)}%` }} />
                        </div>
                        <span className="mt-0.5 truncate text-ink-muted" title={s.ce ? READING[s.ce.reading].long : undefined}>
                          {s.ce ? `${READING[s.ce.reading].short} · OI ${signed(s.ce.oi_change)}` : "—"}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <div className="mt-2 grid grid-cols-[minmax(0,1fr)_4.5rem_minmax(0,1fr)] gap-2 text-2xs text-ink-muted">
                <span className="flex items-center justify-end gap-1"><Swatch side="pe" /> puts</span>
                <span className="text-center">strike</span>
                <span className="flex items-center gap-1"><Swatch side="ce" /> calls</span>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function signed(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const body = a >= 1e7 ? `${(a / 1e7).toFixed(2)}Cr` : a >= 1e5 ? `${(a / 1e5).toFixed(2)}L` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : String(Math.round(a));
  return v > 0 ? `+${body}` : v < 0 ? `−${body}` : "0";
}
