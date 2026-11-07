"use client";

import { useEffect, useState } from "react";

import { Badge, Card } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { builtinDifferences, driftSummary, effectiveValue, formatParam } from "@/lib/programConfig";
import { recordedAt } from "@/lib/recorded";
import type { ProgramConfigReport } from "@/lib/types";

export interface ProgramConfigs {
  programs: ProgramConfigReport[];
  builtin: { name: string; version: string };
}

/** Every uploaded program's configuration, re-read once a minute. */
export function useProgramConfigs(): ProgramConfigs | null {
  const [data, setData] = useState<ProgramConfigs | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const d = await apiGet<ProgramConfigs>("/tuning/programs");
        if (!cancelled) setData(d);
      } catch {
        /* the page still renders the built-in's form */
      }
    };
    void load();
    const t = setInterval(() => void load(), 60_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);
  return data;
}

/**
 * The deck's alarm: a program running different numbers from its own code —
 * started from an older upload, or the code changed since. Loud on purpose:
 * the desk's record of the strategy no longer describes the live process.
 */
export function ConfigDriftNotice() {
  const data = useProgramConfigs();
  const drifted = (data?.programs ?? []).filter((p) => p.drift.length);
  if (!drifted.length) return null;
  return (
    <div role="alert" className="rounded-lg border border-critical/60 bg-critical/15 px-4 py-3 text-xs text-critical">
      <strong className="font-semibold">Running configuration does not match the uploaded code.</strong>{" "}
      {drifted.map((p) => driftSummary(p)).join(" · ")}. Restart the program between sessions to run its
      current code, or upload the version that is running.{" "}
      <a href="/strategy" className="underline underline-offset-2">Compare on Strategy</a>
    </div>
  );
}

/** One uploaded program: what it logged at startup, what its code says, and the built-in's form. */
export function ProgramConfigCard({ report, builtin }: { report: ProgramConfigReport; builtin: ProgramConfigs["builtin"] }) {
  const diffs = builtinDifferences(report);
  return (
    <Card
      title={`${report.name} — what it is running`}
      subtitle={
        report.running
          ? `As it logged at its last startup, ${recordedAt(report.running.logged_ts)} IST${
              report.code ? ` · code v${report.code.version ?? "?"}` : ""
            }`
          : `It has not started yet: these are the constants in its code${report.code ? ` (v${report.code.version ?? "?"})` : ""}`
      }
      action={
        report.drift.length ? (
          <Badge tone="critical">Drift: {report.drift.length}</Badge>
        ) : (
          <Badge tone="good">Runs its code</Badge>
        )
      }
    >
      <p className="mb-3 text-2xs leading-relaxed text-ink-muted">
        This program carries its own parameters in its code and never reads the form below, which belongs to
        the built-in engine ({builtin.name} · {builtin.version}).
        {diffs.length
          ? ` It differs from that form on ${diffs.length} value${diffs.length === 1 ? "" : "s"}: ${diffs.join("; ")}.`
          : " Every value it reports matches that form."}
      </p>
      <div>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-hairline text-left text-2xs uppercase tracking-[0.12em] text-ink-muted">
              <th className="py-1.5 pr-3 font-medium">Parameter</th>
              <th className="py-1.5 pr-3 text-right font-medium">Running</th>
              <th className="py-1.5 pr-3 text-right font-medium">In its code</th>
              <th className="py-1.5 text-right font-medium">Built-in form</th>
            </tr>
          </thead>
          <tbody>
            {report.rows.map((r) => (
              <tr key={r.key} className={`border-b border-hairline last:border-0 ${r.drift ? "bg-critical/10" : ""}`}>
                <td className="py-1.5 pr-3 text-ink">
                  {r.label}
                  {r.drift && <span className="ml-2"><Badge tone="critical">drift</Badge></span>}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink">
                  {r.running_known ? formatParam(r.kind, r.running) : "not logged"}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink-secondary">
                  {r.code_known ? formatParam(r.kind, r.code) : "—"}
                </td>
                <td
                  className={`py-1.5 text-right tabular-nums ${r.differs_from_builtin ? "text-warning" : "text-ink-muted"}`}
                  title={r.differs_from_builtin ? `The program uses ${formatParam(r.kind, effectiveValue(r))}` : undefined}
                >
                  {r.builtin_known ? formatParam(r.kind, r.builtin) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
