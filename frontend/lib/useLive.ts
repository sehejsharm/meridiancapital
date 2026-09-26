"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiGet } from "@/lib/client-api";
import type { EventRow, Snapshot, SystemStatus } from "@/lib/types";

export type ConnectionState = "connecting" | "live" | "polling" | "offline";

const MAX_EVENTS = 400;
const POLL_MS = 5000;
// Reconnect quickly: every second the socket is down the desk falls back to
// slower reads. First retry after a quarter second, then doubling, never more
// than 5s apart, so a restarted server is picked up within 5s of coming back.
const FIRST_RETRY_MS = 250;
const MAX_BACKOFF_MS = 5000;
// A socket can die without closing (a phone back from the background, a
// network hand-over). The server heartbeats every quiet second and says so in
// its hello; this many heartbeats of silence means the socket is dead. A
// server that does not announce one heartbeats every 20s.
const SILENT_HEARTBEATS = 5;
const MIN_SILENCE_MS = 5000;
const LEGACY_SILENCE_MS = 45_000;
// A handshake to a server that has vanished (no refusal, just silence) can
// hang for minutes in the browser; give up on it and try again after this.
const HANDSHAKE_TIMEOUT_MS = 8000;

function nextDelay(attempts: { current: number }): number {
  attempts.current += 1;
  return Math.min(FIRST_RETRY_MS * 2 ** (attempts.current - 1), MAX_BACKOFF_MS);
}

interface Message {
  type: "hello" | "snapshot" | "status" | "events" | "ping";
  data?: unknown;
  heartbeat?: number;
}

export interface Live {
  snapshot: Snapshot | null;
  status: SystemStatus | null;
  events: EventRow[];
  connection: ConnectionState;
  lastUpdate: number | null;
  /** Until this time (ms) the link is (re)establishing: a brief gap is expected
   *  and is not worth an alarm. Set on open and whenever the app comes back to
   *  the foreground. */
  settlingUntil: number;
  refresh: () => void;
}

/** How long a fresh open, or a return from the background, may take to connect
 *  before the desk says anything about it. */
const SETTLE_MS = 4000;

/**
 * Subscribes to the control plane's live feed, falling back to polling when the
 * socket cannot be held open. A trading dashboard that silently stops updating
 * is worse than one that is visibly degraded, so the connection state is part of
 * what this returns rather than hidden.
 */
export function useLive(): Live {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const [settlingUntil, setSettlingUntil] = useState(() => Date.now() + SETTLE_MS);

  const socketRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptsRef = useRef(0);
  const closedRef = useRef(false);
  const settleRef = useRef(settlingUntil);

  const settle = useCallback(() => {
    settleRef.current = Date.now() + SETTLE_MS;
    setSettlingUntil(settleRef.current);
  }, []);

  const mergeEvents = useCallback((incoming: EventRow[]) => {
    if (!incoming.length) return;
    setEvents((prev) => {
      const byId = new Map(prev.map((e) => [e.id, e]));
      for (const e of incoming) byId.set(e.id, e);
      return Array.from(byId.values())
        .sort((a, b) => b.id - a.id)
        .slice(0, MAX_EVENTS);
    });
  }, []);

  const pollOnce = useCallback(async () => {
    try {
      const [snap, evts] = await Promise.all([
        apiGet<{ snapshot: Snapshot | null; status: SystemStatus }>("/snapshot"),
        apiGet<{ events: EventRow[] }>("/events?limit=80"),
      ]);
      setSnapshot(snap.snapshot);
      setStatus(snap.status);
      mergeEvents(evts.events);
      setLastUpdate(Date.now());
      // Reads are landing but the socket is not: once the grace is over that is
      // slow data, not a connection still being set up.
      const settling = Date.now() < settleRef.current;
      setConnection((c) => (c === "live" || (c === "connecting" && settling) ? c : "polling"));
    } catch {
      setConnection((c) => (c === "live" ? c : "offline"));
    }
  }, [mergeEvents]);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    void pollOnce();
    pollRef.current = setInterval(() => void pollOnce(), POLL_MS);
  }, [pollOnce]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const connect = useCallback(async () => {
    if (closedRef.current) return;
    try {
      const res = await fetch("/api/ws-ticket", { method: "POST", cache: "no-store" });
      if (!res.ok) throw new Error("ticket refused");
      const { url } = (await res.json()) as { url: string };

      if (closedRef.current) return;
      const socket = new WebSocket(url);
      // A socket replaced by a newer one hands over quietly: its close is not
      // a drop, so it must not schedule a reconnect of its own.
      const previous = socketRef.current;
      socketRef.current = socket;
      previous?.close();
      let opened = false;
      let heard = Date.now();
      let silenceLimit = LEGACY_SILENCE_MS;

      socket.onopen = () => {
        opened = true;
        attemptsRef.current = 0;
        stopPolling();
        setConnection("live");
        setLastUpdate(Date.now());
      };

      socket.onmessage = (event) => {
        let message: Message;
        try {
          message = JSON.parse(event.data as string) as Message;
        } catch {
          return;
        }
        heard = Date.now();
        switch (message.type) {
          case "hello": {
            if (typeof message.heartbeat === "number" && message.heartbeat > 0) {
              silenceLimit = Math.max(MIN_SILENCE_MS, message.heartbeat * 1000 * SILENT_HEARTBEATS);
            }
            const payload = message.data as {
              snapshot: Snapshot | null;
              status: SystemStatus;
              events: EventRow[];
            };
            setSnapshot(payload.snapshot);
            setStatus(payload.status);
            mergeEvents(payload.events ?? []);
            break;
          }
          case "snapshot":
            setSnapshot(message.data as Snapshot);
            break;
          case "status":
            setStatus(message.data as SystemStatus);
            break;
          case "events":
            mergeEvents(message.data as EventRow[]);
            break;
          case "ping":
            break;
        }
        setLastUpdate(Date.now());
      };

      const watchdog = setInterval(() => {
        if (socketRef.current !== socket) {
          clearInterval(watchdog);
          return;
        }
        const silent = Date.now() - heard;
        const stuck = socket.readyState === WebSocket.CONNECTING && silent > HANDSHAKE_TIMEOUT_MS;
        const dead = socket.readyState === WebSocket.OPEN && silent > silenceLimit;
        if (!stuck && !dead) return;
        // Dead without having said so, or a handshake that will not finish.
        // Closing such a socket can take the browser a long while to confirm,
        // so the reconnect does not wait for it.
        socket.onclose = null;
        socket.close();
        reconnect(false);
      }, 1000);

      const reconnect = (grace: boolean) => {
        clearInterval(watchdog);
        if (socketRef.current !== socket) return;
        socketRef.current = null;
        if (closedRef.current) return;
        // A live socket that closed gets a short grace before the desk says so,
        // so a phone's network hand-over that recovers in a second or two
        // raises no alarm. It is given once per drop, not per failed retry, or
        // a real outage would stay hidden behind it; and not to a socket the
        // watchdog ended, whose data is already stale.
        if (opened) {
          setConnection("connecting");
          if (grace) settle();
        }
        // Keep the screen fed by direct reads, and try again soon.
        startPolling();
        retryRef.current = setTimeout(() => void connect(), nextDelay(attemptsRef));
      };

      socket.onclose = () => reconnect(true);
      socket.onerror = () => socket.close();
    } catch {
      startPolling();
      retryRef.current = setTimeout(() => void connect(), nextDelay(attemptsRef));
    }
  }, [mergeEvents, settle, startPolling, stopPolling]);

  // A phone suspends sockets in the background and does not always say so. On
  // return, reconnect at once instead of waiting out a backoff, and read the
  // current state straight away so the screen is right before the socket is.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible" || closedRef.current) return;
      const open = socketRef.current?.readyState === WebSocket.OPEN;
      void pollOnce();
      // A socket that still reads as open may have died while the app was
      // suspended; if so the watchdog ends it within a second of silence
      // running out, under this grace.
      settle();
      if (!open) {
        if (retryRef.current) clearTimeout(retryRef.current);
        attemptsRef.current = 0;
        const stale = socketRef.current;
        socketRef.current = null;
        stale?.close();
        void connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onVisible);
    window.addEventListener("online", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onVisible);
      window.removeEventListener("online", onVisible);
    };
  }, [connect, pollOnce, settle]);

  useEffect(() => {
    closedRef.current = false;
    // Paint from a direct read while the socket is still being set up: the
    // ticket and the handshake take a round trip each, the read takes one.
    void pollOnce();
    void connect();
    return () => {
      closedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      stopPolling();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect, stopPolling]);

  return {
    snapshot, status, events, connection, lastUpdate, settlingUntil,
    refresh: () => void pollOnce(),
  };
}
