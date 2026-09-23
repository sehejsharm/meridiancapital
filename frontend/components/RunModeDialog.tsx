"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui";

/**
 * Asks which money an algorithm is about to trade.
 *
 * This is the only thing standing between a tap and a real order, so the two
 * answers are given equal weight and neither is preselected — a dialog with a
 * highlighted default is a dialog people dismiss without reading. Paper is
 * listed first because it is the one you want when you are unsure.
 */
export function RunModeDialog({
  name,
  open,
  busy = false,
  onPick,
  onCancel,
}: {
  name: string;
  open: boolean;
  busy?: boolean;
  onPick: (mode: "paper" | "live") => void;
  onCancel: () => void;
}) {
  const [confirmingLive, setConfirmingLive] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  // Reopening must never inherit the previous answer's half-made decision.
  useEffect(() => {
    if (open) setConfirmingLive(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  const pickLive = useCallback(() => {
    if (confirmingLive) onPick("live");
    else setConfirmingLive(true);
  }, [confirmingLive, onPick]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-4 backdrop-blur-sm sm:items-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-mode-title"
        tabIndex={-1}
        className="w-full max-w-md rounded-xl border border-hairline bg-surface p-5 shadow-2xl outline-none"
      >
        <h2 id="run-mode-title" className="text-sm font-semibold text-ink">
          How should {name} run?
        </h2>
        <p className="mt-1.5 text-xs text-ink-secondary">
          It will keep starting itself at the open, in this mode, until you stop it.
        </p>

        <div className="mt-5 space-y-2.5">
          <button
            type="button"
            disabled={busy}
            onClick={() => onPick("paper")}
            className="min-h-[56px] w-full rounded-lg border border-hairline bg-surface-raised px-4 py-3 text-left transition-colors hover:border-brand focus:border-brand focus:outline-none disabled:opacity-50"
          >
            <span className="block text-xs font-semibold text-ink">Paper</span>
            <span className="mt-0.5 block text-2xs text-ink-muted">
              Simulated fills. No orders reach the broker.
            </span>
          </button>

          <button
            type="button"
            disabled={busy}
            onClick={pickLive}
            className={`min-h-[56px] w-full rounded-lg border px-4 py-3 text-left transition-colors focus:outline-none disabled:opacity-50 ${
              confirmingLive
                ? "border-critical bg-critical/15"
                : "border-hairline bg-surface-raised hover:border-critical focus:border-critical"
            }`}
          >
            <span className="block text-xs font-semibold text-critical">
              {confirmingLive ? "Tap again to trade real money" : "Real money"}
            </span>
            <span className="mt-0.5 block text-2xs text-ink-muted">
              Live orders on your Angel One account, with real capital at risk.
            </span>
          </button>
        </div>

        <div className="mt-5 flex justify-end">
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}
