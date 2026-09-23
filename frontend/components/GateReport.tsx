"use client";

import { Badge } from "@/components/ui";
import type { GateReport as Report } from "@/lib/types";

/**
 * The verdict on an uploaded algorithm.
 *
 * Failures come first and stay expanded: this is the screen someone reads when
 * their strategy was refused, and the reason has to be the thing they see, not
 * something behind a toggle. Each check cites the requirement it came from so
 * a rejection is arguable rather than mysterious.
 */
export function GateReport({ report }: { report: Report }) {
  const checks = [...(report.checks ?? [])].sort(
    (a, b) => Number(a.passed) - Number(b.passed),
  );
  const scanFailed = report.scan && !report.scan.ok;

  return (
    <div className="space-y-4">
      <div
        className={`rounded-lg border px-4 py-3 ${
          report.passed
            ? "border-good/40 bg-good/10"
            : "border-critical/40 bg-critical/10"
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p
            className={`text-xs font-semibold uppercase tracking-[0.12em] ${
              report.passed ? "text-good" : "text-critical"
            }`}
          >
            {report.passed ? "Passed every check" : "Flagged — worth a look before you run it"}
          </p>
          {report.total > 0 && (
            <span className="text-2xs text-ink-muted">
              {report.total - report.failed} of {report.total} checks passed
            </span>
          )}
        </div>
        {report.error && (
          <p className="mt-2 text-xs text-critical">{report.error}</p>
        )}
      </div>

      {scanFailed && (
        <div className="rounded-lg border border-critical/40 bg-surface p-4">
          <h3 className="text-2xs font-semibold uppercase tracking-[0.14em] text-critical">
            Static screening
          </h3>
          <p className="mt-1 text-2xs text-ink-muted">
            Refused before the module was imported — a module body runs on import,
            so this check happens first.
          </p>
          <ul className="mt-2 space-y-1">
            {report.scan!.errors.map((e) => (
              <li key={e} className="font-mono text-2xs text-critical">
                {e}
              </li>
            ))}
          </ul>
        </div>
      )}

      {checks.length > 0 && (
        <ul className="divide-y divide-hairline rounded-lg border border-hairline bg-surface">
          {checks.map((c) => (
            <li key={c.key} className="px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-ink">{c.title}</p>
                  <p
                    className={`mt-1 text-2xs ${
                      c.passed ? "text-ink-muted" : "text-critical"
                    }`}
                  >
                    {c.detail}
                  </p>
                  <p className="mt-1 text-2xs italic text-ink-muted">{c.spec}</p>
                </div>
                <Badge tone={c.passed ? "good" : "critical"}>
                  {c.passed ? "pass" : "fail"}
                </Badge>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
