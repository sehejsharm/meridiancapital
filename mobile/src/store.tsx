import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { AppState, AppStateStatus, Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

import * as api from './api';
import * as bio from './biometrics';
import { registerForPush } from './push';
import type { BotStatus, EquityMark, FeedEvent, Snapshot } from './types';

const KEY_URL = 'meridian_url';
const KEY_TOKEN = 'meridian_token';
const KEY_USER = 'meridian_user';
const MAX_LINES = 900;

type Connection = 'connecting' | 'live' | 'polling' | 'offline';

interface Store {
  ready: boolean;
  authed: boolean;
  connection: Connection;
  baseUrl: string;
  user: string;
  savedServer: string;
  savedUser: string;
  /** A stored session plus enrolled biometrics — offer the unlock. */
  canUnlock: boolean;
  status: BotStatus | null;
  snapshot: Snapshot;
  feed: FeedEvent[];
  marks: EquityMark[];
  error: string | null;

  /** Password sign-in, or token sign-in when `token` is supplied. */
  login(url: string, username: string, password: string, token?: string): Promise<void>;
  unlockWithBiometrics(): Promise<boolean>;
  enableBiometrics(): Promise<boolean>;
  disableBiometrics(): Promise<void>;
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
  const [user, setUser] = useState('');
  const [savedServer, setSavedServer] = useState('');
  const [savedUser, setSavedUser] = useState('');
  const [canUnlock, setCanUnlock] = useState(false);
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

  const normalise = (url: string) => {
    let u = url.trim().replace(/\/+$/, '');
    if (u && !/^https?:\/\//.test(u)) u = `https://${u}`;
    return u;
  };

  const persist = async (url: string, token: string, who: string) => {
    await SecureStore.setItemAsync(KEY_URL, url).catch(() => {});
    await SecureStore.setItemAsync(KEY_TOKEN, token).catch(() => {});
    await SecureStore.setItemAsync(KEY_USER, who).catch(() => {});
    setSavedServer(url);
    setSavedUser(who);
  };

  const login = useCallback(async (
    url: string, username: string, password: string, token?: string,
  ) => {
    const clean = normalise(url);
    if (!clean) throw new Error('Enter the server address');

    let sessionToken: string;
    let who: string;

    if (token) {
      // Straight API token — validate it before storing.
      api.configure(clean, token.trim());
      const me = await api.whoami();
      sessionToken = token.trim();
      who = me.user;
    } else {
      const session = await api.login(clean, username.trim(), password);
      sessionToken = session.token;
      who = session.user;
      api.configure(clean, sessionToken);
    }

    await persist(clean, sessionToken, who);
    setUser(who);
    await begin(clean, sessionToken);

    // Offer to turn on Face ID once there is a session worth protecting.
    const cap = await bio.capability();
    if (cap.available && cap.enrolled && !(await bio.isEnabled())) {
      setCanUnlock(false);   // stays off until the user opts in from Control
    }
  }, [begin]);

  const unlockWithBiometrics = useCallback(async (): Promise<boolean> => {
    const url = await SecureStore.getItemAsync(KEY_URL).catch(() => null);
    const token = await SecureStore.getItemAsync(KEY_TOKEN).catch(() => null);
    const who = await SecureStore.getItemAsync(KEY_USER).catch(() => null);
    if (!url || !token) return false;

    const ok = await bio.authenticate('Unlock Meridian Capital');
    if (!ok) return false;

    api.configure(url, token);
    try {
      await api.whoami();          // the stored session may have expired
    } catch {
      return false;
    }
    setUser(who ?? '');
    await begin(url, token);
    return true;
  }, [begin]);

  const enableBiometrics = useCallback(async (): Promise<boolean> => {
    const cap = await bio.capability();
    if (!cap.available || !cap.enrolled) return false;
    const ok = await bio.authenticate(`Enable ${cap.label} for Meridian`);
    if (!ok) return false;
    await bio.setEnabled(true);
    setCanUnlock(true);
    return true;
  }, []);

  const disableBiometrics = useCallback(async () => {
    await bio.setEnabled(false);
    setCanUnlock(false);
  }, []);

  const disconnect = useCallback(async () => {
    closeSocket();
    if (pollTimer.current) { clearInterval(pollTimer.current); pollTimer.current = null; }
    await SecureStore.deleteItemAsync(KEY_TOKEN).catch(() => {});
    await bio.setEnabled(false);
    api.configure('', '');
    setAuthed(false);
    setUser('');
    setCanUnlock(false);
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
        const url = await SecureStore.getItemAsync(KEY_URL).catch(() => null);
        const token = await SecureStore.getItemAsync(KEY_TOKEN).catch(() => null);
        const who = await SecureStore.getItemAsync(KEY_USER).catch(() => null);
        setSavedServer(url ?? '');
        setSavedUser(who ?? '');

        if (!url || !token) return;

        const cap = await bio.capability();
        const bioOn = await bio.isEnabled();

        if (bioOn && cap.available && cap.enrolled) {
          // Locked: the gate offers Face ID rather than opening straight up.
          setCanUnlock(true);
          return;
        }

        api.configure(url, token);
        try {
          await api.whoami();
          setUser(who ?? '');
          await begin(url, token);
        } catch {
          setAuthed(false);   // stored session no longer valid
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
    ready, authed, connection, baseUrl, user, savedServer, savedUser, canUnlock,
    status, snapshot, feed, marks, error,
    login, unlockWithBiometrics, enableBiometrics, disableBiometrics,
    disconnect, refresh, clearFeed,
  }), [ready, authed, connection, baseUrl, user, savedServer, savedUser, canUnlock,
       status, snapshot, feed, marks, error,
       login, unlockWithBiometrics, enableBiometrics, disableBiometrics,
       disconnect, refresh, clearFeed]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
