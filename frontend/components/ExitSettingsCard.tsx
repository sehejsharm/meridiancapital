"use client";

import { useEffect, useState } from "react";

import { Badge, Button, Card, Empty, Skeleton } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { changes, problemWith, shown, type ExitSettingsView } from "@/lib/exitSettings";

const input = "w-full rounded-md border border-hairline bg-surface-raised px-3 py-2 text-sm text-ink tabular-nums outline-none focus:border-brand";

/** The exit figures a v14 program starts with. Changing one is deliberate: a typed phrase, the program stopped, the program's own ranges. */
export function ExitSettingsCard({ algoId, name }: { algoId: string; name: string }) {
  const [view, setView] = useState<ExitSettingsView | null>(null);
  const [failed, setFailed] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const take = (v: ExitSettingsView) => {
    setView(v);
    setForm(Object.fromEntries(v.settings.map((s) => [s.key, s.next_start === null ? "" : shown(s, s.next_start).replace(/[% ]|min/g, "")])));
  };

  useEffect(() => {
    setView(null);
    setFailed(false);
    setNote(null);
    apiGet<ExitSettingsView>(`/control/exit-settings?algo_id=${encodeURIComponent(algoId)}`).then(take).catch(() => setFailed(true));
  }, [algoId]);

  const title = "Exit settings";
  if (failed) return <Card title={title}><Empty>This is not on the server yet. Update the server to switch it on.</Empty></Card>;
  if (!view) return <Card title={title}><Skeleton rows={4} /></Card>;

  if (!view.supported) {
    return (
      <Card title={title} subtitle={name}>
        <Empty>
          This version keeps its exit settings in its code. From v14 they can be set here without a new upload. Nothing can be
          changed for this version.
        </Empty>
      </Card>
    );
  }

  const pending = changes(view.settings, form);
  const invalid = view.settings.some((s) => problemWith(s, form[s.key] ?? "") !== null);
  const count = Object.keys(pending).length;
  const locked = view.running || busy;

  const send = async (path: string, body: object, done: string) => {
    setBusy(true);
    setNote(null);
    try {
      take(await apiPost<ExitSettingsView>(path, body));
      setConfirm("");
      setNote(done);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Nothing was changed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title={title}
      subtitle={`${name}: read once, when the program starts`}
      action={<Badge tone={view.changed.length ? "warning" : "good"}>{view.changed.length ? `${view.changed.length} changed` : "Built-in"}</Badge>}
    >
      <p className="text-xs leading-relaxed text-ink-secondary">
        Only the built-in values have a backtest behind them. A different value here is your decision and is untested unless you
        tested it. The entry rules and position sizing cannot be changed here.
      </p>
      {view.problem && (
        <p className="mt-2 text-xs text-critical">
          The settings file on the server is not usable ({view.problem}). The program will ignore it and start with the built-in values.
        </p>
      )}
      {view.running && (
        <p className="mt-2 text-xs text-warning">
          The program is running, so these cannot be changed now: it read them when it started. Stop it after the close first.
        </p>
      )}
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {view.settings.map((s) => {
          const problem = problemWith(s, form[s.key] ?? "");
          const differs = s.built_in !== null && s.next_start !== null && Math.abs(s.built_in - s.next_start) > 1e-9;
          return (
            <label key={s.key} className="block min-w-0 text-xs">
              <span className="text-ink-muted">{s.label} ({s.minutes ? "min" : "%"})</span>
              <input inputMode="decimal" value={form[s.key] ?? ""} disabled={locked} aria-invalid={problem !== null}
                onChange={(e) => setForm({ ...form, [s.key]: e.target.value })} className={`mt-1 ${input}`} />
              <span className={`mt-1 block text-2xs ${problem ? "text-critical" : differs ? "text-warning" : "text-ink-muted"}`}>
                {problem ?? `Built-in ${shown(s, s.built_in)}${differs ? `, set to ${shown(s, s.next_start)}` : ""}; allowed ${shown(s, s.low)} to ${shown(s, s.high)}`}
              </span>
            </label>
          );
        })}
      </div>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="block text-xs">
          <span className="text-ink-muted">Type {view.phrase} to change</span>
          <input value={confirm} disabled={locked} onChange={(e) => setConfirm(e.target.value)} className={`mt-1 ${input}`} autoComplete="off" />
        </label>
        <Button variant="danger" disabled={locked || invalid || count === 0 || confirm !== view.phrase}
          onClick={() => void send("/control/exit-settings", { algo_id: algoId, confirm, values: pending }, "Saved. In force from the next start.")}>
          {busy ? "Saving…" : count ? `Change ${count} setting${count === 1 ? "" : "s"}` : "Nothing to change"}
        </Button>
        <Button disabled={locked || view.changed.length === 0 && !view.problem}
          onClick={() => void send("/control/exit-settings/reset", { algo_id: algoId }, "Back to the built-in values.")}>
          Back to built-in
        </Button>
      </div>
      {note && <p className="mt-2 text-xs text-ink-secondary">{note}</p>}
    </Card>
  );
}
