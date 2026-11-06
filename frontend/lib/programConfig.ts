import type { ProgramConfigReport, ProgramConfigRow, TuningKind } from "@/lib/types";

/**
 * How a strategy parameter reads on the desk, whichever engine it belongs to.
 * A missing take-profit (`null`) is "none" — the program runs without one —
 * not a blank that could be mistaken for "not loaded".
 */
export function formatParam(kind: TuningKind, value: number | string | null | undefined): string {
  if (value === null) return "none";
  if (value === undefined || value === "") return "—";
  if (kind === "time") return String(value);
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (kind === "pct") {
    const p = Math.round(n * 1000) / 10;
    return `${Number.isInteger(p) ? p.toFixed(0) : p.toFixed(1)}%`;
  }
  if (kind === "money") return `₹${n.toLocaleString("en-IN")}`;
  return String(n);
}

/** What the program is using: its startup log if it has started, else its code. */
export function effectiveValue(row: ProgramConfigRow): number | string | null | undefined {
  if (row.running_known) return row.running;
  if (row.code_known) return row.code;
  return undefined;
}

/** One line for the deck and the page: is the live program running what the desk holds? */
export function driftSummary(report: ProgramConfigReport): string | null {
  if (!report.drift.length) return null;
  const parts = report.rows
    .filter((r) => r.drift)
    .map((r) => `${r.label} ${formatParam(r.kind, r.running)} running vs ${formatParam(r.kind, r.code)} in its code`);
  return `${report.name}: ${parts.join("; ")}`;
}

/** The parameters a program runs that differ from the built-in engine's form. */
export function builtinDifferences(report: ProgramConfigReport): string[] {
  return report.rows
    .filter((r) => r.differs_from_builtin)
    .map((r) => `${r.label}: ${formatParam(r.kind, effectiveValue(r))} (built-in ${formatParam(r.kind, r.builtin)})`);
}
