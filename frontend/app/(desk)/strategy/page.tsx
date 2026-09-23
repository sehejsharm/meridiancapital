"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { useLiveFeed } from "@/lib/LiveContext";
import type { TuningParam, TuningPayload } from "@/lib/types";

type Notice = { tone: "good" | "critical"; text: string } | null;
type Draft = Record<string, string>;

/** Server values arrive typed; the form edits them as strings and parses on save. */
function toDraft(values: Record<string, number | string>): Draft {
  return Object.fromEntries(Object.entries(values).map(([k, v]) => [k, String(v)]));
}

function formatValue(p: TuningParam, raw: number | string): string {
  if (p.kind === "time") return String(raw);
  const n = Number(raw);
  if (!Number.isFinite(n)) return String(raw);
  if (p.kind === "pct") return `${(n * 100).toFixed(0)}%`;
  if (p.kind === "money") return `Rs ${n.toLocaleString("en-IN")}`;
  return String(n);
}

/** Percentages are stored as fractions but are far easier to read and type as whole percent. */
function displayFor(p: TuningParam, v: string): string {
  if (p.kind !== "pct" || v === "") return v;
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n * 1000) / 10) : v;
}

function storeFor(p: TuningParam, v: string): string {
  if (p.kind !== "pct" || v === "") return v;
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n * 10) / 1000) : v;
}

export default function StrategyPage() {
  const { refresh } = useLiveFeed();
  const [data, setData] = useState<TuningPayload | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [confirm, setConfirm] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const payload = await apiGet<TuningPayload>("/tuning");
      setData(payload);
      setDraft(toDraft(payload.effective));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load strategy parameters");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Which fields the operator has actually moved away from what is saved.
  const dirty = useMemo(() => {
    if (!data) return [];
    return data.params.filter((p) => draft[p.key] !== String(data.effective[p.key]));
  }, [data, draft]);

  // Which of those move risk up — mirrors engine.tuning.riskier_keys, so the
  // form can ask for the phrase before the API has to refuse the write.
  const riskier = useMemo(() => {
    if (!data) return [];
    return data.params.filter((p) => {
      const now = draft[p.key];
      const def = String(data.defaults[p.key]);
      if (now === undefined || now === def || now === "") return false;
      if (p.kind === "time") return now > def;
      const a = Number(now);
      const b = Number(def);
      if (!Number.isFinite(a)) return false;
      return a > b === p.riskier_up;
    });
  }, [data, draft]);

  const needsPhrase = riskier.length > 0;
  const phraseOk = !needsPhrase || confirm === (data?.confirm_phrase ?? "RETUNE");

  const save = useCallback(async () => {
    if (!data) return;
    setBusy(true);
    setNotice(null);
    try {
      const values: Record<string, string | number> = {};
      for (const p of data.params) {
        const raw = draft[p.key];
        values[p.key] = p.kind === "time" ? raw : Number(raw);
      }
      const payload = await apiPost<TuningPayload>("/tuning", { values, confirm });
      setData(payload);
      setDraft(toDraft(payload.effective));
      setConfirm("");
      setNotice({
        tone: "good",
        text: payload.engine_running
          ? "Saved. The engine binds its parameters at startup, so restart it to apply."
          : "Saved. These values will be used the next time the engine starts.",
      });
      refresh();
    } catch (e) {
      setNotice({ tone: "critical", text: e instanceof Error ? e.message : "save failed" });
    } finally {
      setBusy(false);
    }
  }, [confirm, data, draft, refresh]);

  const reset = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const payload = await apiPost<TuningPayload>("/tuning/reset");
      setData(payload);
      setDraft(toDraft(payload.effective));
      setConfirm("");
      setNotice({ tone: "good", text: "Restored the backtested configuration." });
      refresh();
    } catch (e) {
      setNotice({ tone: "critical", text: e instanceof Error ? e.message : "reset failed" });
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  if (error) {
    return (
      <div className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">
        {error}
      </div>
    );
  }
  if (!data) {
    return <div className="text-xs text-ink-muted">Loading strategy parameters…</div>;
  }

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-xs text-warning">
        <strong className="font-semibold">These numbers are the strategy.</strong> Every value
        here was fixed by the backtest that produced this build&apos;s results. Changing any of
        them means the engine is no longer running the configuration that was validated, and the
        published figures no longer describe it. Changes are written to the audit log and take
        effect the next time the engine starts — never under an open position.
      </div>

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

      {data.pending_restart && (
        <div className="rounded-lg border border-serious/40 bg-serious/10 px-4 py-3 text-xs text-serious">
          The running engine is still on the parameters it started with. Restart it from Controls
          to pick up the {data.changed.length} saved override
          {data.changed.length === 1 ? "" : "s"}.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={data.changed.length ? "warning" : "good"}>
          {data.changed.length
            ? `${data.changed.length} overridden`
            : "Backtested configuration"}
        </Badge>
        {data.changed.length > 0 && (
          <Button variant="ghost" onClick={reset} disabled={busy}>
            Reset all to backtested
          </Button>
        )}
      </div>

      {data.groups.map((group) => (
        <Card key={group} title={group}>
          <div className="space-y-4">
            {data.params
              .filter((p) => p.group === group)
              .map((p) => {
                const isDefault = draft[p.key] === String(data.defaults[p.key]);
                const isRiskier = riskier.some((r) => r.key === p.key);
                return (
                  <div
                    key={p.key}
                    className="grid gap-x-4 gap-y-1.5 border-b border-hairline pb-4 last:border-0 last:pb-0 sm:grid-cols-[1fr_auto]"
                  >
                    <div className="min-w-0">
                      <label
                        htmlFor={p.key}
                        className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink"
                      >
                        {p.label}
                        {!isDefault && (
                          <Badge tone={isRiskier ? "critical" : "warning"}>
                            {isRiskier ? "riskier" : "changed"}
                          </Badge>
                        )}
                      </label>
                      <p className="mt-1 text-2xs leading-relaxed text-ink-muted">{p.help}</p>
                    </div>

                    <div className="flex items-center gap-2 sm:justify-end">
                      <input
                        id={p.key}
                        type={p.kind === "time" ? "time" : "number"}
                        inputMode={p.kind === "time" ? undefined : "decimal"}
                        value={displayFor(p, draft[p.key] ?? "")}
                        step={p.kind === "pct" ? 1 : p.step}
                        min={p.kind === "pct" ? p.min * 100 : p.min}
                        max={p.kind === "pct" ? p.max * 100 : p.max}
                        onChange={(e) =>
                          setDraft((d) => ({ ...d, [p.key]: storeFor(p, e.target.value) }))
                        }
                        className={`w-28 rounded-md border bg-surface-raised px-2.5 py-1.5 text-right text-xs tabular-nums text-ink outline-none focus:border-brand touch:min-h-[44px] ${
                          isDefault ? "border-hairline" : "border-warning/60"
                        }`}
                      />
                      {p.kind === "pct" && <span className="text-2xs text-ink-muted">%</span>}
                      <span className="w-28 shrink-0 text-2xs text-ink-muted">
                        backtested {formatValue(p, data.defaults[p.key])}
                      </span>
                    </div>
                  </div>
                );
              })}
          </div>
        </Card>
      ))}

      <Card title="Apply">
        <div className="space-y-3">
          {dirty.length === 0 ? (
            <p className="text-xs text-ink-muted">No unsaved changes.</p>
          ) : (
            <p className="text-xs text-ink-secondary">
              {dirty.length} unsaved change{dirty.length === 1 ? "" : "s"}:{" "}
              <span className="text-ink">{dirty.map((p) => p.label).join(", ")}</span>
            </p>
          )}

          {needsPhrase && (
            <div className="space-y-2 rounded-md border border-critical/40 bg-critical/10 p-3">
              <p className="text-xs text-critical">
                {riskier.map((p) => p.label).join(", ")} move risk{" "}
                <strong className="font-semibold">up</strong> relative to the backtested build.
                Type <code className="font-mono">{data.confirm_phrase}</code> to confirm.
              </p>
              <input
                aria-label="Confirmation phrase"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder={data.confirm_phrase}
                className="w-40 rounded-md border border-hairline bg-surface-raised px-2.5 py-1.5 font-mono text-xs uppercase tracking-widest text-ink outline-none focus:border-brand"
              />
            </div>
          )}

          <Button
            variant="primary"
            onClick={save}
            disabled={busy || dirty.length === 0 || !phraseOk}
          >
            {busy ? "Saving…" : "Save parameters"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
