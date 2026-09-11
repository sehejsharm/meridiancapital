"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiGet } from "@/lib/client-api";
import type { EventRow, Snapshot, SystemStatus } from "@/lib/types";

export type ConnectionState = "connecting" | "live" | "polling" | "offline";

const MAX_EVENTS = 400;
const POLL_MS = 5000;
const MAX_BACKOFF_MS = 30_000;

interface Message {
  type: "hello" | "snapshot" | "status" | "events" | "ping";
  data?: unknown;
}

export interface Live {
  snapshot: Snapshot | null;
  status: SystemStatus | null;
  events: EventRow[];
  connection: ConnectionState;
  lastUpdate: number | null;
  refresh: () => void;
}

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

  const socketRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptsRef = useRef(0);
  const closedRef = useRef(false);

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
      setConnection((c) => (c === "live" ? c : "polling"));
    } catch {
      setConnection("offline");
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

      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => {
        attemptsRef.current = 0;
        stopPolling();
        setConnection("live");
      };

      socket.onmessage = (event) => {
        let message: Message;
        try {
          message = JSON.parse(event.data as string) as Message;
        } catch {
          return;
        }
        switch (message.type) {
          case "hello": {
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

      const reconnect = () => {
        socketRef.current = null;
        if (closedRef.current) return;
        startPolling();
        attemptsRef.current += 1;
        const delay = Math.min(1000 * 2 ** attemptsRef.current, MAX_BACKOFF_MS);
        retryRef.current = setTimeout(() => void connect(), delay);
      };

      socket.onclose = reconnect;
      socket.onerror = () => socket.close();
    } catch {
      startPolling();
      attemptsRef.current += 1;
      const delay = Math.min(1000 * 2 ** attemptsRef.current, MAX_BACKOFF_MS);
      retryRef.current = setTimeout(() => void connect(), delay);
    }
  }, [mergeEvents, startPolling, stopPolling]);

  useEffect(() => {
    closedRef.current = false;
    void connect();
    return () => {
      closedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      stopPolling();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect, stopPolling]);

  return { snapshot, status, events, connection, lastUpdate, refresh: () => void pollOnce() };
}
