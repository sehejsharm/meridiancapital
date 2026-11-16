"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { keyBytes, pushSupport, type PushSupport } from "@/lib/phone";

interface PushStatus { public_key: string; devices: { label: string; added: string | null; service: string | null }[] }

function deviceName(): string {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android phone";
  return /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows PC" : "browser";
}

/** Notifications on this device, sent by the desk itself: no Telegram account needed. */
export function PhoneAlerts() {
  const [support, setSupport] = useState<PushSupport | null>(null);
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [here, setHere] = useState<string | null>(null);     // this device's subscription address, if it has one
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const nav = navigator as Navigator & { standalone?: boolean };
    setSupport(pushSupport({
      hasServiceWorker: "serviceWorker" in navigator,
      hasPushManager: "PushManager" in window,
      hasNotification: "Notification" in window,
      permission: "Notification" in window ? Notification.permission : null,
      isIos: /iPhone|iPad|iPod/.test(navigator.userAgent),
      standalone: nav.standalone === true || window.matchMedia("(display-mode: standalone)").matches,
    }));
    void apiGet<PushStatus>("/push").then(setStatus).catch(() => setStatus(null));
    if ("serviceWorker" in navigator) {
      void navigator.serviceWorker.ready
        .then((reg) => reg.pushManager?.getSubscription())
        .then((sub) => setHere(sub?.endpoint ?? null))
        .catch(() => undefined);
    }
  }, []);

  const run = async (job: () => Promise<string>) => {
    setBusy(true);
    setNote(null);
    try {
      setNote(await job());
    } catch (e) {
      setNote(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const turnOn = () => run(async () => {
    if (!status) return "The server has not been updated yet.";
    if ((await Notification.requestPermission()) !== "granted") {
      setSupport("blocked");
      return "Notifications were not allowed.";
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(status.public_key) as BufferSource });
    const json = sub.toJSON();
    setStatus(await apiPost<PushStatus>("/push/subscribe", { endpoint: sub.endpoint, keys: json.keys, label: deviceName() }));
    setHere(sub.endpoint);
    return "On. Send a test to see it arrive.";
  });

  const turnOff = () => run(async () => {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      setStatus(await apiPost<PushStatus>("/push/unsubscribe", { endpoint: sub.endpoint }));
      await sub.unsubscribe();
    }
    setHere(null);
    return "Off on this device.";
  });

  const test = () => run(async () => {
    const r = await apiPost<{ ok: boolean; detail?: string | null }>("/push/test");
    return r.ok ? "Sent. It should appear within a few seconds." : (r.detail ?? "The test did not go through.");
  });

  const count = status?.devices.length ?? 0;
  return (
    <div className="mt-4 border-t border-hairline pt-3">
      <h3 className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-secondary">Notifications from the desk itself</h3>
      <p className="mt-1 text-xs text-ink">
        {status === null ? "Not on this server yet: update the server to switch it on."
          : count === 0 ? "No device has asked for them."
            : `${count} device${count === 1 ? "" : "s"}: ${status.devices.map((d) => d.label).join(", ")}`}
      </p>
      {support === "install-first" && (
        <p className="mt-2 text-2xs leading-relaxed text-ink-muted">
          On an iPhone, first add the desk to the home screen (Share, then Add to Home Screen) and open it from there. Apple does
          not allow notifications from a website in a Safari tab.
        </p>
      )}
      {support === "unsupported" && <p className="mt-2 text-2xs text-ink-muted">This browser cannot take notifications.</p>}
      {support === "blocked" && (
        <p className="mt-2 text-2xs leading-relaxed text-ink-muted">
          Notifications are blocked for this site on this device. Allow them in the device&apos;s settings, then come back.
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {support === "ready" && status && (here
          ? <Button onClick={() => void turnOff()} disabled={busy}>Turn off on this device</Button>
          : <Button variant="primary" onClick={() => void turnOn()} disabled={busy}>Turn on on this device</Button>)}
        <Button onClick={() => void test()} disabled={busy || count === 0}>Send a test</Button>
        {note && <span className="text-2xs text-ink-secondary">{note}</span>}
      </div>
    </div>
  );
}
