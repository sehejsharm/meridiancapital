import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { AppState, AppStateStatus, Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

import * as api from './api';
import { registerForPush } from './push';
import type { BotStatus, EquityMark, FeedEvent, Snapshot } from './types';

const KEY_URL = 'meridian_url';
const KEY_TOKEN = 'meridian_token';
const MAX_LINES = 900;

type Connection = 'connecting' | 'live' | 'polling' | 'offline';

interface Store {
  ready: boolean;
  authed: boolean;
  connection: Connection;
  baseUrl: string;
  status: BotStatus | null;
  snapshot: Snapshot;
  feed: FeedEvent[];
  marks: EquityMark[];
  error: string | null;

  connect(url: string, token: string): Promise<void>;
  disconnect(): Promise<void>;
  refresh(): Promise<void>;
  clearFeed(): void;
}

const Ctx = createContext<Store | null>(null);

export const useStore = () => {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useStore must be used inside <StoreProvider>');
  return ctx;
};

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [connection, setConnection] = useState<Connection>('offline');
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot>({});
  const [feed, setFeed] = useState<FeedEvent[]>([]);
  const [marks, setMarks] = useState<EquityMark[]>([]);
  const [error, setError] = useState<string | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const retry = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const alive = useRef(true);

  useEffect(() => () => { alive.current = false; }, []);

  // ---------------------------------------------------------------- feed

  const pushEvents = useCallback((incoming: FeedEvent[]) => {
    if (!incoming.length) return;
    setFeed((prev) => {
      const next = [...prev, ...incoming];
      return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
    });
  }, []);

  const clearFeed = useCallback(() => setFeed([]), []);

  // ---------------------------------------------------------------- refresh

  const refresh = useCallback(async () => {
    try {
      const s = await api.status();
      if (!alive.current) return;
      setStatus(s);
      if (s.snapshot && Object.keys(s.snapshot).length) setSnapshot(s.snapshot);
      setError(null);
    } catch (e: any) {
      if (e?.status === 401) {
        await disconnect();
        setError('Token rejected — reconnect with a valid token.');
      } else {
        setError(e?.message ?? 'Cannot reach the server');
      }
    }
  }, []);

  const refreshMarks = useCallback(async () => {
    try {
      const d = await api.intradayEquity();
      if (alive.current) setMarks(d.marks ?? []);
    } catch {
      /* the curve is decoration; failure here is not worth surfacing */
    }
  }, []);

  // ---------------------------------------------------------------- socket

  const closeSocket = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (ws.current) {
      ws.current.onclose = null;
      try { ws.current.close(); } catch { /* already gone */ }
      ws.current = null;
    }
  }, []);

  const openSocket = useCallback(() => {
    closeSocket();
    if (!api.getToken()) return;

    setConnection('connecting');
    let socket: WebSocket;
    try {
      socket = new WebSocket(api.websocketUrl());
    } catch {
      setConnection('polling');
      return;
    }
    ws.current = socket;

    socket.onopen = () => {
      retry.current = 0;
      setConnection('live');
    };

    socket.onmessage = (ev) => {
      let msg: any;
      try { msg = JSON.parse(ev.data as string); } catch { return; }

      if (msg.kind === 'ping') return;

      if (msg.kind === 'hello') {
        if (msg.status) {
          setStatus(msg.status);
          if (msg.status.snapshot) setSnapshot(msg.status.snapshot);
        }
        if (Array.isArray(msg.tail)) setFeed(msg.tail.slice(-MAX_LINES));
        return;
      }

      if (msg.kind === 'status') {
        setSnapshot(msg.payload ?? {});
        return;
      }

      pushEvents([msg as FeedEvent]);

      // Events that change durable state warrant a status/curve re-read.
      if (['entry', 'exit', 'eod', 'daily_kill', 'boot', 'ready', 'shutdown',
           'supervisor', 'scheduler', 'fatal'].includes(msg.kind)) {
        refresh();
      }
      if (msg.kind === 'minute' || msg.kind === 'exit') refreshMarks();
    };

    socket.onerror = () => { /* onclose runs next and owns the retry */ };

    socket.onclose = () => {
      if (!alive.current || !api.getToken()) return;
      setConnection('polling');
      retry.current = Math.min(retry.current + 1, 6);
      const delay = Math.round(1000 * Math.pow(1.7, retry.current));
      reconnectTimer.current = setTimeout(openSocket, delay);
    };
  }, [closeSocket, pushEvents, refresh, refreshMarks]);

  // ---------------------------------------------------------------- session

  const begin = useCallback(async (url: string, token: string) => {
    api.configure(url, token);
    setBaseUrl(url);
    setAuthed(true);
    await refresh();
    await refreshMarks();
    openSocket();

    if (pollTimer.current) clearInterval(pollTimer.current);
    // A safety net under the socket: even if it silently dies, the app stays
    // no more than 20 seconds stale.
    pollTimer.current = setInterval(() => {
      refresh();
      if (connectionIsStale()) openSocket();
    }, 20000);

    registerForPush().then((pushToken) => {
      if (pushToken) api.registerPush(pushToken, Platform.OS).catch(() => {});
    });
  }, [openSocket, refresh, refreshMarks]);

  const connectionIsStale = () =>
    ws.current == null || ws.current.readyState > 1; // CLOSING or CLOSED

  const connect = useCallback(async (url: string, token: string) => {
    const clean = url.trim().replace(/\/+$/, '');
    api.configure(clean, token.trim());
    await api.status(); // throws if the token or URL is wrong
    await SecureStore.setItemAsync(KEY_URL, clean);
    await SecureStore.setItemAsync(KEY_TOKEN, token.trim());
    await begin(clean, token.trim());
  }, [begin]);

  const disconnect = useCallback(async () => {
    closeSocket();
    if (pollTimer.current) { clearInterval(pollTimer.current); pollTimer.current = null; }
    await SecureStore.deleteItemAsync(KEY_TOKEN).catch(() => {});
    api.configure('', '');
    setAuthed(false);
    setStatus(null);
    setSnapshot({});
    setFeed([]);
    setMarks([]);
    setConnection('offline');
  }, [closeSocket]);

  // ---------------------------------------------------------------- boot

  useEffect(() => {
    (async () => {
      try {
        const url = await SecureStore.getItemAsync(KEY_URL);
        const token = await SecureStore.getItemAsync(KEY_TOKEN);
        if (url && token) {
          api.configure(url, token);
          try {
            await api.status();
            await begin(url, token);
          } catch {
            // Stored credentials no longer work; fall through to the gate.
            setAuthed(false);
          }
        }
      } finally {
        setReady(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Coming back from the background: reconnect immediately rather than
  // waiting out the backoff.
  useEffect(() => {
    const onChange = (state: AppStateStatus) => {
      if (state === 'active' && authed) {
        refresh();
        refreshMarks();
        if (connectionIsStale()) openSocket();
      }
    };
    const sub = AppState.addEventListener('change', onChange);
    return () => sub.remove();
  }, [authed, openSocket, refresh, refreshMarks]);

  useEffect(() => () => {
    closeSocket();
    if (pollTimer.current) clearInterval(pollTimer.current);
  }, [closeSocket]);

  const value = useMemo<Store>(() => ({
    ready, authed, connection, baseUrl, status, snapshot, feed, marks, error,
    connect, disconnect, refresh, clearFeed,
  }), [ready, authed, connection, baseUrl, status, snapshot, feed, marks, error,
       connect, disconnect, refresh, clearFeed]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
