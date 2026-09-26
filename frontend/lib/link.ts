import { useLiveFeed } from "@/lib/LiveContext";
import type { ConnectionState } from "@/lib/useLive";
import { DEAD_MS, STALE_MS, useTickAge } from "@/lib/useTickAge";

export type LinkView = {
  /** Short label for the header and the deck, or null to show nothing. */
  label: string | null;
  tone: "good" | "warning" | "critical" | "neutral";
  /** True while a fresh open or a return from the background is still
   *  connecting: a gap then is expected and raises no alarm. */
  settling: boolean;
};

/**
 * What the desk says about its own link to the server, in one place.
 *
 * The data is what matters, not the transport: if a direct read has just
 * landed while the socket is still shaking hands, the numbers on screen are
 * current, and flashing "Connecting" over them only reads as a fault. The
 * desk speaks up once a problem has lasted past the settling window.
 */
export function linkView(
  connection: ConnectionState,
  lastUpdate: number | null,
  settlingUntil: number,
  now: number = Date.now(),
): LinkView {
  const settling = now < settlingUntil && connection !== "live";
  const age = lastUpdate === null ? null : now - lastUpdate;
  const fresh = age !== null && age <= STALE_MS;

  if (connection === "live") {
    if (age !== null && age > DEAD_MS) return { label: "No data", tone: "critical", settling };
    return { label: "Live data", tone: fresh ? "good" : "warning", settling };
  }
  if (settling) return fresh ? { label: "Live data", tone: "good", settling } : { label: null, tone: "neutral", settling };
  if (connection === "polling") return { label: "Slow data", tone: "warning", settling };
  if (connection === "offline") return { label: "Offline", tone: "critical", settling };
  return { label: "Reconnecting", tone: "warning", settling };
}

/**
 * The link view, kept current as the link ages. Without the tick a view only
 * changes when the connection state does, so a desk whose server went quiet
 * kept reading "Connected" for as long as the outage lasted.
 */
export function useLinkView(): LinkView {
  const { connection, lastUpdate, settlingUntil } = useLiveFeed();
  useTickAge(lastUpdate);
  return linkView(connection, lastUpdate, settlingUntil);
}

/** Milliseconds since a naive IST timestamp, as the engines write them. */
export function istAgeMs(ts: string | null | undefined, now: number = Date.now()): number | null {
  if (!ts) return null;
  const withZone = /[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts : `${ts}+05:30`;
  const t = Date.parse(withZone);
  return Number.isFinite(t) ? now - t : null;
}
