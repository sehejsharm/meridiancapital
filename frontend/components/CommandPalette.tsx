"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { fetchAlgos } from "@/lib/algos";
import { algoCommands, baseCommands, matchCommands, type Command } from "@/lib/commands";

function istToday(): string {
  return new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);
}

/**
 * Ctrl+K / ⌘K: jump anywhere on the desk. Starting or stopping an algorithm
 * opens its page, where the confirmation is — a search box is too easy to
 * fat-finger for anything that moves money.
 */
export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [algos, setAlgos] = useState<{ id: string; name: string; running: boolean }[]>([]);
  const input = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    setTimeout(() => input.current?.focus(), 0);
    void fetchAlgos()
      .then((l) => setAlgos(l.algos.map((a) => ({ id: a.id, name: a.name, running: a.runtime.running }))))
      .catch(() => setAlgos([]));
  }, [open]);

  const items = useMemo(
    () => matchCommands([...baseCommands(istToday()), ...algoCommands(algos)], query),
    [algos, query],
  );

  const go = (c: Command | undefined) => {
    if (!c) return;
    setOpen(false);
    router.push(c.href);
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[12vh]" onClick={() => setOpen(false)}>
      <div
        role="dialog"
        aria-label="Command palette"
        className="w-full max-w-lg overflow-hidden rounded-lg border border-brand/40 bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={input}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((i) => Math.min(items.length - 1, i + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => Math.max(0, i - 1));
            } else if (e.key === "Enter") {
              go(items[active]);
            }
          }}
          placeholder="Go to a page or an algorithm…"
          aria-label="Search the desk"
          className="w-full border-b border-hairline bg-transparent px-4 py-3 text-sm text-ink outline-none placeholder:text-ink-muted"
        />
        <ul className="max-h-80 overflow-y-auto py-1" role="listbox">
          {items.length === 0 && <li className="px-4 py-3 text-xs text-ink-muted">Nothing matches.</li>}
          {items.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === active}>
              <button
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => go(c)}
                className={`flex w-full items-baseline justify-between gap-3 px-4 py-2 text-left text-xs ${
                  i === active ? "bg-brand-dim text-brand" : "text-ink"
                }`}
              >
                <span>{c.label}</span>
                <span className="truncate text-2xs text-ink-muted">{c.hint}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="border-t border-hairline px-4 py-2 text-2xs text-ink-muted">↑↓ to move · Enter to open · Esc to close</div>
      </div>
    </div>
  );
}
