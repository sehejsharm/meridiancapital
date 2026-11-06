"use client";

import { useEffect, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";

/** Where the desk's alerts go, and a way to prove it. The secrets stay on the server. */
export function AlertChannels() {
  const [channels, setChannels] = useState<{ telegram: boolean; webhook: boolean } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void apiGet<{ channels: { telegram: boolean; webhook: boolean } }>("/reliability/alerts")
      .then((d) => setChannels(d.channels))
      .catch(() => setChannels(null));
  }, []);

  const test = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await apiPost<{ ok: boolean; detail?: string }>("/reliability/alerts/test");
      setNote(r.ok ? "Sent — check your phone." : (r.detail ?? "The test did not go through."));
    } catch (e) {
      setNote(e instanceof Error ? e.message : "The test did not go through.");
    } finally {
      setBusy(false);
    }
  };

  const any = channels && (channels.telegram || channels.webhook);
  return (
    <Card
      title="Alerts"
      subtitle="Trade entered or exited, any kill switch, a crash or crash loop, automation armed or disarmed, an NSE holiday ahead"
      action={channels ? <Badge tone={any ? "good" : "warning"}>{any ? "On" : "Not set up"}</Badge> : undefined}
    >
      <dl className="grid grid-cols-2 gap-2 text-xs">
        <div><dt className="text-ink-muted">Telegram</dt><dd className="text-ink">{channels ? (channels.telegram ? "configured" : "off") : "—"}</dd></div>
        <div><dt className="text-ink-muted">Webhook (WhatsApp / email bridge)</dt><dd className="text-ink">{channels ? (channels.webhook ? "configured" : "off") : "—"}</dd></div>
      </dl>
      {!any && channels && (
        <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
          Set <code>MERIDIAN_TELEGRAM_BOT_TOKEN</code> and <code>MERIDIAN_TELEGRAM_CHAT_ID</code> (or{" "}
          <code>MERIDIAN_ALERT_WEBHOOK</code>) in <code>meridian.env</code> on the server and restart meridian-api.
        </p>
      )}
      <div className="mt-3 flex items-center gap-3">
        <Button onClick={() => void test()} disabled={busy || !any}>{busy ? "Sending…" : "Send a test alert"}</Button>
        {note && <span className="text-2xs text-ink-secondary">{note}</span>}
      </div>
    </Card>
  );
}
