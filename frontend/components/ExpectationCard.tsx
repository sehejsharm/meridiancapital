"use client";

import { useEffect, useState } from "react";

import { Badge, Button, Card, Empty, Skeleton } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { band, verdict, verdictText } from "@/lib/expectation";
import type { BandPoint, ExpectationView } from "@/lib/expectation";
import { dayLabel, pnlClass, signedMoney } from "@/lib/format";

const input = "mt-1 w-full rounded-md border border-hairline bg-surface-raised px-3 py-2 text-sm text-ink outline-none focus:border-brand";
const W = 100;
const H = 44;

function xy(points: BandPoint[], pick: (p: BandPoint) => number, lo: number, span: number): string[] {
  const step = points.length > 1 ? W / (points.length - 1) : 0;
  return points.map((p, i) => `${(i * step).toFixed(2)},${(H - 2 - ((pick(p) - lo) / span) * (H - 4)).toFixed(2)}`);
}

function path(points: BandPoint[], pick: (p: BandPoint) => number, lo: number, span: number): string {
  return `M${xy(points, pick, lo, span).join(" L")}`;
}

function Chart({ points }: { points: BandPoint[] }) {
  const all = points.flatMap((p) => [p.actual, p.low, p.high]);
  const lo = Math.min(...all);
  const span = Math.max(...all) - lo || 1;
  const shade = `M${[...xy(points, (p) => p.high, lo, span), ...xy(points, (p) => p.low, lo, span).reverse()].join(" L")} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-44 w-full" role="img"
      aria-label="Live running total against the range the backtest allows">
      <path d={shade} className="text-ink-muted" fill="currentColor" opacity={0.14} />
      <path d={path(points, (p) => p.expected, lo, span)} className="text-ink-muted" fill="none" stroke="currentColor"
        strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      <path d={path(points, (p) => p.actual, lo, span)} className="text-brand" fill="none" stroke="currentColor"
        strokeWidth={1.8} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Live results against the range the backtest allows for. The figures are the operator's; none is built in. */
export function ExpectationCard() {
  const [view, setView] = useState<ExpectationView | null>(null);
  const [failed, setFailed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ mean: "", sd: "", since: "", label: "" });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiGet<ExpectationView>("/research/expectation").then(setView).catch(() => setFailed(true));
  }, []);

  const title = "Live against the backtest";
  if (failed) return <Card title={title}><Empty>This is not on the server yet. Update the server to switch it on.</Empty></Card>;
  if (!view) return <Card title={title}><Skeleton rows={4} /></Card>;

  const exp = view.expectation;
  const points = exp ? band(exp, view.trades) : [];
  const last = points[points.length - 1];
  const v = verdict(points);

  const open = () => {
    setForm({ mean: exp ? String(exp.mean) : "", sd: exp ? String(exp.sd) : "", since: exp?.since ?? new Date().toISOString().slice(0, 10), label: exp?.label ?? "" });
    setError("");
    setEditing(true);
  };
  const save = async () => {
    const mean = Number(form.mean);
    const sd = Number(form.sd);
    if (!Number.isFinite(mean) || !Number.isFinite(sd) || sd < 50 || !form.since) {
      setError("Enter the average per trade, the swing per trade (at least 50) and a start date.");
      return;
    }
    setSaving(true);
    try {
      setView(await apiPost<ExpectationView>("/research/expectation", { mean, sd, since: form.since, label: form.label }));
      setEditing(false);
    } catch {
      setError("The server did not accept these figures.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title={title}
      subtitle={exp ? `${exp.label || "Backtest"}: ${signedMoney(exp.mean)} a trade on average, ${signedMoney(exp.sd).replace("+", "±")} typical swing, one lot; counted from ${dayLabel(exp.since)}` : "Is live trading doing what the backtest said it would?"}
      action={v === "none" || !exp ? undefined : <Badge tone={v === "below" ? "critical" : v === "early" ? "neutral" : "good"}>{v === "early" ? "too early" : `${v} the band`}</Badge>}
    >
      {!exp ? (
        <Empty>No backtest figures are on record. Enter the average result and the swing of one trade from the backtest, and the date to count from.</Empty>
      ) : (
        <>
          {points.length >= 2 && <Chart points={points} />}
          {last && (
            <dl className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
              <div><dt className="text-ink-muted">Trades</dt><dd className="text-base font-semibold tabular-nums">{last.n}</dd></div>
              <div><dt className="text-ink-muted">Live total</dt><dd className={`text-base font-semibold tabular-nums ${pnlClass(last.actual)}`}>{signedMoney(last.actual)}</dd></div>
              <div><dt className="text-ink-muted">Backtest expects</dt><dd className="text-base font-semibold tabular-nums">{signedMoney(last.expected)}</dd></div>
              <div><dt className="text-ink-muted">Luck allows</dt><dd className="text-sm font-semibold tabular-nums">{signedMoney(last.low)} to {signedMoney(last.high)}</dd></div>
            </dl>
          )}
          <p className="mt-3 text-xs leading-relaxed text-ink-secondary">{verdictText(v, last?.n ?? 0)}</p>
          <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
            Solid line: real-money results after charges. Dashed: the backtest average. Shaded: two standard deviations either side, which
            luck alone covers about 19 times in 20.
          </p>
        </>
      )}

      {editing ? (
        <div className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <label className="block"><span className="text-ink-muted">Average per trade, ₹</span>
            <input inputMode="decimal" value={form.mean} onChange={(e) => setForm({ ...form, mean: e.target.value })} className={input} /></label>
          <label className="block"><span className="text-ink-muted">Swing per trade, ₹</span>
            <input inputMode="decimal" value={form.sd} onChange={(e) => setForm({ ...form, sd: e.target.value })} className={input} /></label>
          <label className="block"><span className="text-ink-muted">Count from</span>
            <input type="date" value={form.since} onChange={(e) => setForm({ ...form, since: e.target.value })} className={input} /></label>
          <label className="block"><span className="text-ink-muted">Name</span>
            <input value={form.label} maxLength={80} onChange={(e) => setForm({ ...form, label: e.target.value })} className={input} /></label>
          {error && <p className="col-span-full text-critical">{error}</p>}
          <div className="col-span-full flex gap-2">
            <Button variant="primary" onClick={save} disabled={saving}>Save</Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="mt-4"><Button onClick={open}>{exp ? "Change the backtest figures" : "Enter the backtest figures"}</Button></div>
      )}
    </Card>
  );
}
