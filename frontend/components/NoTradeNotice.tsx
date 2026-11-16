"use client";

import { useEffect, useState } from "react";

import { apiGet } from "@/lib/client-api";
import { expiryNotice, type ExpiryOutlook } from "@/lib/expiry";
import { pollWhileVisible } from "@/lib/pollWhileVisible";

/**
 * Whether the program can trade today, from its own expiry window: on a day when
 * the nearest contracts are too close to or too far from expiry (7 or 8 days out,
 * say), it takes no trade, and the desk says so up front.
 */
export function NoTradeNotice({ always = false }: { always?: boolean }) {
  const [outlook, setOutlook] = useState<ExpiryOutlook | null>(null);
  useEffect(() => pollWhileVisible(() => { apiGet<ExpiryOutlook>("/market/expiry-outlook").then(setOutlook).catch(() => setOutlook(null)); }, 300_000), []);
  const n = expiryNotice(outlook);
  if (!n || (n.tone === "ok" && !always)) return null;
  const blocked = n.tone === "blocked";
  return (
    <div role="status" className={`rounded-lg border px-4 py-3 text-xs ${blocked ? "border-warning/50 bg-warning/10" : "border-hairline bg-surface"}`}>
      <p className={`font-semibold ${blocked ? "text-warning" : "text-ink"}`}>{n.title}</p>
      <p className="mt-0.5 text-ink-secondary">{n.detail}</p>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs">
        {n.week.map((d, i) => (
          <li key={i} className={d.ok ? "text-ink-secondary" : "text-warning"}>
            <span className="font-semibold">{d.label}</span> {d.text}
          </li>
        ))}
      </ul>
    </div>
  );
}
