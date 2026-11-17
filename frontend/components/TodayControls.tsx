"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, Card } from "@/components/ui";
import { apiPost } from "@/lib/client-api";
import { controlTargets, defaultTarget } from "@/lib/controlTargets";
import { useLiveFeed } from "@/lib/LiveContext";

const PHRASE = "FLATTEN";
const input = "mt-1 w-full rounded-md border border-hairline bg-surface-raised px-3 py-2 text-sm text-ink outline-none focus:border-brand";

/**
 * The three controls a phone needs, on the phone screen: halt new entries, sell the
 * open trade and keep running, sell and stop. The last two need the phrase typed, as
 * on Controls. They act on the program trading real money (else the one running).
 */
export function TodayControls({ holding }: { holding: boolean }) {
  const { status } = useLiveFeed();
  const targets = controlTargets(status?.fleet?.algos).filter((t) => t.running);
  const id = defaultTarget(targets);
  const target = targets.find((t) => t.algoId === id);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const act = async (key: string, path: string, body: object) => {
    setBusy(key);
    setNote(null);
    try {
      const r = await apiPost<{ detail?: string }>(path, body);
      setNote({ ok: true, text: r.detail ?? "Done." });
      setConfirm("");
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "That did not go through." });
    } finally {
      setBusy(null);
    }
  };

  if (!target) {
    return (
      <Card title="Controls">
        <p className="text-xs text-ink-muted">Nothing is running, so there is nothing to halt or sell.</p>
        <Link href="/controls" className="mt-3 inline-block text-xs font-semibold text-brand">All controls</Link>
      </Card>
    );
  }

  const algo = target.kind === "uploaded" ? { algo_id: target.algoId } : {};
  const phraseOk = confirm.trim() === PHRASE;
  return (
    <Card title="Controls" subtitle={`${target.name}, ${target.mode === "live" ? "real money" : "paper"}`}>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Button full disabled={busy !== null} onClick={() => void act("halt", "/control/halt", algo)}>
          {busy === "halt" ? "Sending…" : "Halt new entries"}
        </Button>
        <Button full variant="danger" disabled={busy !== null || !holding || !phraseOk}
          onClick={() => void act("sell", "/control/sell-now", { confirm: PHRASE, ...algo, algo_id: target.algoId })}>
          {busy === "sell" ? "Sending…" : "Sell now, keep running"}
        </Button>
        <Button full variant="danger" disabled={busy !== null || !phraseOk}
          onClick={() => void act("flatten", "/control/flatten", { confirm: PHRASE, ...algo })}>
          {busy === "flatten" ? "Sending…" : "Sell and stop"}
        </Button>
      </div>
      <label className="mt-3 block text-xs">
        <span className="text-ink-muted">Type {PHRASE} to sell</span>
        <input value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" autoCapitalize="characters" className={input} />
      </label>
      {note && <p className={`mt-2 text-xs ${note.ok ? "text-ink-secondary" : "text-critical"}`}>{note.text}</p>}
      <p className="mt-2 text-2xs leading-relaxed text-ink-muted">
        Halt: no new trade; an open one is still managed. Sell now: v13 programs and later sell through their own exit and stay up.
        Sell and stop: the program sells on its way out and stays off today. <Link href="/controls" className="text-brand">All controls</Link>
      </p>
    </Card>
  );
}
