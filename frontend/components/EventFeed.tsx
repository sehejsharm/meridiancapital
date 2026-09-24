"use client";

import { useMemo, useState } from "react";

import type { EventLevel, EventRow } from "@/lib/types";
import { istTime } from "@/lib/format";
import { Card, Empty } from "@/components/ui";

const LEVEL_STYLE: Record<EventLevel, { dot: string; text: string; label: string }> = {
  debug: { dot: "bg-ink-muted", text: "text-ink-muted", label: "DEBUG" },
  info: { dot: "bg-ink-muted", text: "text-ink-secondary", label: "INFO" },
  ok: { dot: "bg-good", text: "text-ink", label: "OK" },
  warn: { dot: "bg-warning", text: "text-ink", label: "WARN" },
  error: { dot: "bg-critical", text: "text-ink", label: "ERROR" },
  critical: { dot: "bg-critical", text: "text-critical", label: "CRITICAL" },
};

const FILTERS = [
  { id: "all", label: "All", levels: null },
  { id: "activity", label: "Activity", levels: ["ok", "warn", "error", "critical"] },
  { id: "alerts", label: "Alerts", levels: ["warn", "error", "critical"] },
] as const;

export function EventFeed({
  events,
  limit = 40,
  title = "Event log",
  subtitle,
  algoNames,
  emptyText = "Nothing logged at this level yet.",
}: {
  events: EventRow[];
  limit?: number;
  title?: string;
  subtitle?: string;
  /** When given, each line is tagged with the strategy that wrote it. */
  algoNames?: Record<string, string>;
  emptyText?: string;
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("activity");

  const shown = useMemo(() => {
    const active = FILTERS.find((f) => f.id === filter);
    const levels = active?.levels;
    const filtered = levels
      ? events.filter((e) => (levels as readonly string[]).includes(e.level))
      : events;
    return filtered.slice(0, limit);
  }, [events, filter, limit]);

  return (
    <Card
      title={title}
      subtitle={subtitle}
      action={
        <div className="flex gap-1 rounded-md border border-hairline p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={`rounded px-2 py-1 text-2xs font-medium uppercase tracking-[0.1em] transition-colors touch:min-h-[40px] touch:px-3 ${
                filter === f.id
                  ? "bg-brand-dim text-brand"
                  : "text-ink-muted hover:text-ink-secondary"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      }
    >
      {shown.length === 0 ? (
        <Empty>{emptyText}</Empty>
      ) : (
        <ol className="max-h-[28rem] space-y-0 overflow-y-auto">
          {shown.map((event) => {
            const style = LEVEL_STYLE[event.level] ?? LEVEL_STYLE.info;
            return (
              <li
                key={event.id}
                className="flex gap-3 border-b border-hairline py-2 last:border-0"
              >
                <span className="w-16 shrink-0 pt-0.5 text-2xs tabular-nums text-ink-muted">
                  {istTime(event.ts)}
                </span>
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1">
                  <span className="sr-only">{style.label}: </span>
                  <span className={`block break-words text-xs leading-relaxed ${style.text}`}>
                    {event.message}
                  </span>
                  {(algoNames || event.source !== "engine") && (
                    <span className="flex flex-wrap gap-x-2 text-2xs uppercase tracking-[0.1em] text-ink-muted">
                      {algoNames && event.algo_id && (
                        <span className="text-brand">
                          {algoNames[event.algo_id] ?? event.algo_id}
                        </span>
                      )}
                      {event.source !== "engine" && <span>{event.source}</span>}
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
