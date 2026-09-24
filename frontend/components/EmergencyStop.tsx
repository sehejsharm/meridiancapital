"use client";

import { useCallback, useState } from "react";

import { apiPost } from "@/lib/client-api";

const PHRASE = "NUKE ALL";

interface NukeResult {
  engines_stopped: { algo_id: string }[];
  had_open_positions: string[];
  /** Standalone programs: they report no position, so it is unknown. Absent
   *  from control planes older than this field. */
  position_unknown?: string[];
  automation_disarmed: boolean;
  detail: string;
}

/**
 * The last resort.
 *
 * Deliberately two steps: the button arms a panel, the panel wants the phrase.
 * Something that stops every engine and market-exits every position must not be
 * reachable by one mis-tap on a phone in a pocket.
 *
 * The result is kept on screen afterwards rather than flashed as a toast,
 * because the one thing worth reading is which algorithms were holding a
 * position when it fired — those exits need checking in the broker's own app.
 */
export function EmergencyStop({ onDone }: { onDone?: () => void }) {
  const [armed, setArmed] = useState(false);
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<NukeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fire = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await apiPost<NukeResult>("/control/nuke", { confirm: phrase });
      setResult(out);
      setArmed(false);
      setPhrase("");
      onDone?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "the emergency stop failed");
    } finally {
      setBusy(false);
    }
  }, [phrase, onDone]);

  return (
    <section
      aria-labelledby="estop-heading"
      className="rounded-lg border-2 border-critical/70 bg-critical/5 p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2
            id="estop-heading"
            className="text-sm font-bold uppercase tracking-[0.16em] text-critical"
          >
            Emergency stop
          </h2>
          <p className="mt-1 max-w-[60ch] text-2xs leading-relaxed text-ink-secondary">
            Queues a market exit for every open position, stops every engine, and
            disarms automation so nothing restarts. Exits are sent first — a stopped
            engine cannot square off its own position.
          </p>
        </div>
        {!armed && !result && (
          <button
            type="button"
            onClick={() => setArmed(true)}
            className="min-h-[44px] shrink-0 rounded-md border-2 border-critical bg-critical/15 px-5 py-2.5 text-sm font-bold uppercase tracking-[0.14em] text-critical transition-colors hover:bg-critical/25"
          >
            Stop everything
          </button>
        )}
      </div>

      {armed && (
        <div className="mt-4 space-y-3 border-t border-critical/40 pt-4">
          <p className="text-xs text-critical">
            Type <code className="font-mono font-bold">{PHRASE}</code> to confirm. This
            cannot be undone, and it will not restart on its own.
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              id="estop-phrase"
              aria-label="Confirmation phrase"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              placeholder={PHRASE}
              autoComplete="off"
              className="min-h-[44px] min-w-0 flex-1 rounded-md border border-critical/60 bg-surface px-3 py-2 font-mono text-sm uppercase tracking-widest text-ink outline-none focus:border-critical"
            />
            <button
              type="button"
              onClick={fire}
              disabled={busy || phrase !== PHRASE}
              className="min-h-[44px] rounded-md border-2 border-critical bg-critical px-5 py-2 text-sm font-bold uppercase tracking-[0.14em] text-[color:var(--plane)] transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "Stopping…" : "Confirm"}
            </button>
            <button
              type="button"
              onClick={() => {
                setArmed(false);
                setPhrase("");
              }}
              disabled={busy}
              className="min-h-[44px] rounded-md px-4 py-2 text-xs uppercase tracking-[0.12em] text-ink-secondary hover:text-ink"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-xs text-critical">
          {error}
        </p>
      )}

      {result && (
        <div className="mt-4 space-y-2 border-t border-critical/40 pt-4 text-xs">
          <p className="font-semibold text-ink">{result.detail}</p>
          {result.had_open_positions.length > 0 && (
            <p className="text-critical">
              Verify in the Angel One app that the exit filled for:{" "}
              <span className="font-mono">{result.had_open_positions.join(", ")}</span>.
              This queues market orders — it cannot guarantee they filled.
            </p>
          )}
          {(result.position_unknown?.length ?? 0) > 0 && (
            <p className="text-critical">
              Standalone program{result.position_unknown!.length === 1 ? "" : "s"}{" "}
              <span className="font-mono">{result.position_unknown!.join(", ")}</span> cannot report
              a position to the desk. {result.position_unknown!.length === 1 ? "It was" : "They were"}{" "}
              told to square off on stop — open the Angel One app and confirm nothing is left open.
            </p>
          )}
          <button
            type="button"
            onClick={() => setResult(null)}
            className="text-2xs uppercase tracking-[0.12em] text-ink-muted hover:text-ink"
          >
            Dismiss
          </button>
        </div>
      )}
    </section>
  );
}
