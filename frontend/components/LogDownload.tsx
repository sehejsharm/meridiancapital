"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Card } from "@/components/ui";
import { apiGet } from "@/lib/client-api";

interface LogSummary {
  day: string;
  algo_id: string;
  events: number;
  by_level: Record<string, number>;
  errors: number;
  first_ts: string | null;
  last_ts: string | null;
}

function istToday(): string {
  // The desk runs on IST; the browser may not.
  const now = new Date();
  const ist = new Date(now.getTime() + (330 + now.getTimezoneOffset()) * 60_000);
  return ist.toISOString().slice(0, 10);
}

/**
 * A day's log, as a file.
 *
 * The summary loads before you download so you can see whether the day is
 * worth keeping — an empty session and one with fourteen errors should not
 * look the same from the outside.
 *
 * Downloads are plain links to the relay rather than fetch-and-blob, which
 * keeps the session cookie doing the authenticating and lets the browser name
 * the file.
 */
export function LogDownload({ algoId }: { algoId?: string }) {
  const [day, setDay] = useState(istToday);
  const [summary, setSummary] = useState<LogSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(() => {
    const p = new URLSearchParams({ day });
    if (algoId) p.set("algo_id", algoId);
    return p.toString();
  }, [day, algoId]);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSummary(await apiGet<LogSummary>(`/logs?${query}`));
    } catch (e) {
      setSummary(null);
      setError(e instanceof Error ? e.message : "could not read that day");
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const linkClass =
    "inline-flex min-h-[40px] items-center rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-brand/50";

  return (
    <Card
      title="Daily log"
      subtitle="Every event the desk recorded on a given day"
      action={
        summary ? (
          <Badge tone={summary.errors > 0 ? "critical" : summary.events ? "good" : "neutral"}>
            {summary.events} event{summary.events === 1 ? "" : "s"}
          </Badge>
        ) : null
      }
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-2xs uppercase tracking-[0.12em] text-ink-muted">Date</span>
          <input
            id="log-day"
            type="date"
            value={day}
            max={istToday()}
            onChange={(e) => setDay(e.target.value)}
            className="min-h-[40px] rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
          />
        </label>

        <a className={linkClass} href={`/api/proxy/logs.csv?${query}`}>
          Download CSV
        </a>
        <a className={linkClass} href={`/api/proxy/logs.txt?${query}`}>
          Download text
        </a>
      </div>

      {error ? (
        <p className="mt-3 text-xs text-critical">{error}</p>
      ) : summary ? (
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 border-t border-hairline pt-3 text-2xs text-ink-muted">
          {summary.events === 0 ? (
            <span>Nothing was recorded on this date.</span>
          ) : (
            <>
              <span>
                First <b className="tabular-nums text-ink">{summary.first_ts?.slice(11, 19)}</b>
              </span>
              <span>
                Last <b className="tabular-nums text-ink">{summary.last_ts?.slice(11, 19)}</b>
              </span>
              {Object.entries(summary.by_level)
                .sort()
                .map(([level, count]) => (
                  <span key={level}>
                    {level}{" "}
                    <b
                      className={`tabular-nums ${
                        level === "error" || level === "critical" ? "text-critical" : "text-ink"
                      }`}
                    >
                      {count}
                    </b>
                  </span>
                ))}
            </>
          )}
        </div>
      ) : (
        <p className="mt-3 text-2xs text-ink-muted">Loading…</p>
      )}
    </Card>
  );
}
