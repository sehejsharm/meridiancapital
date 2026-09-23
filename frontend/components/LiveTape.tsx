"use client";

import { useEffect, useRef, useState } from "react";

import { Card, Empty } from "@/components/ui";
import { istTime } from "@/lib/format";
import type { EventRow } from "@/lib/types";

const LEVEL_TONE: Record<string, string> = {
  critical: "text-critical",
  error: "text-critical",
  warn: "text-warning",
  ok: "text-good",
  info: "text-ink-secondary",
};

const LEVEL_DOT: Record<string, string> = {
  critical: "bg-critical",
  error: "bg-critical",
  warn: "bg-warning",
  ok: "bg-good",
  info: "bg-ink-muted",
};

/**
 * Everything the desk is doing, newest first, arriving as it happens.
 *
 * New rows are highlighted briefly so a change is noticeable without watching.
 * Auto-scroll pauses the moment the operator scrolls away from the top, because
 * yanking the view back while someone is reading an error is worse than being
 * a few rows behind.
 */
export function LiveTape({
  events,
  height = 340,
  title = "Live updates",
}: {
  events: EventRow[];
  height?: number;
  title?: string;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);
  const [flash, setFlash] = useState<Set<number>>(new Set());
  const seen = useRef<Set<number>>(new Set());

  useEffect(() => {
    const fresh = events.filter((e) => !seen.current.has(e.id)).map((e) => e.id);
    if (!fresh.length) return;
    // First render is not "new"; only mark rows that arrived while watching.
    const isFirst = seen.current.size === 0;
    for (const id of fresh) seen.current.add(id);
    if (isFirst) return;

    setFlash(new Set(fresh));
    const t = setTimeout(() => setFlash(new Set()), 1400);
    return () => clearTimeout(t);
  }, [events]);

  useEffect(() => {
    if (pinned && box.current) box.current.scrollTop = 0;
  }, [events, pinned]);

  return (
    <Card
      title={title}
      subtitle={`${events.length} in view`}
      action={
        !pinned ? (
          <button
            type="button"
            onClick={() => {
              setPinned(true);
              if (box.current) box.current.scrollTop = 0;
            }}
            className="rounded-md border border-hairline px-2 py-1 text-2xs uppercase tracking-[0.1em] text-ink-secondary hover:text-ink touch:min-h-[40px] touch:px-3"
          >
            Jump to newest
          </button>
        ) : null
      }
    >
      {!events.length ? (
        <Empty>Nothing yet. Events appear the moment an engine reports one.</Empty>
      ) : (
        <div
          ref={box}
          onScroll={(e) => setPinned(e.currentTarget.scrollTop < 24)}
          className="-mx-1 overflow-y-auto px-1"
          style={{ maxHeight: height }}
          role="log"
          aria-live="polite"
        >
          <ul className="divide-y divide-hairline">
            {events.map((e) => (
              <li
                key={e.id}
                className={`flex gap-2.5 py-2 transition-colors duration-700 ${
                  flash.has(e.id) ? "bg-brand-dim" : ""
                }`}
              >
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                    LEVEL_DOT[e.level] ?? "bg-ink-muted"
                  }`}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <p className={`text-xs leading-snug ${LEVEL_TONE[e.level] ?? "text-ink"}`}>
                    {e.message}
                  </p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-2xs text-ink-muted">
                    <span className="tabular-nums">{istTime(e.ts)}</span>
                    <span aria-hidden="true">·</span>
                    <span>{e.source}</span>
                    {e.algo_id ? (
                      <>
                        <span aria-hidden="true">·</span>
                        <span className="truncate">{e.algo_id}</span>
                      </>
                    ) : null}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
