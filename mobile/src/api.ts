import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';

import type {
  BotStatus, ChartData, DateRange, EquityMark, FeedEvent, Format,
  Period, SessionRow, StrategyDescription, Summary, Trade,
} from './types';

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

let BASE = '';
let TOKEN = '';

export function configure(baseUrl: string, token: string) {
  BASE = baseUrl.trim().replace(/\/+$/, '');
  TOKEN = token.trim();
}

export const getBase = () => BASE;
export const getToken = () => TOKEN;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!BASE) throw new ApiError('No server configured');

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const res = await fetch(BASE + path, {
      ...init,
      signal: controller.signal,
      headers: {
        'X-API-Token': TOKEN,
        'Content-Type': 'application/json',
        ...(init.headers ?? {}),
      },
    });
    if (res.status === 401) throw new ApiError('Token rejected by the server', 401);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = body.detail ?? body.reason ?? detail;
      } catch {
        /* body was not JSON */
      }
      throw new ApiError(detail, res.status);
    }
    return (await res.json()) as T;
  } catch (err: any) {
    if (err instanceof ApiError) throw err;
    if (err?.name === 'AbortError') throw new ApiError('Server did not respond in time');
    throw new ApiError(err?.message ?? 'Network error');
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------- auth

export interface Session {
  token: string;
  expires_at: number;
  user: string;
}

/**
 * Sign in. Deliberately does not use `request`, because the base URL is being
 * established by this very call and there is no token yet.
 */
export async function login(
  baseUrl: string, username: string, password: string,
): Promise<Session> {
  const clean = baseUrl.trim().replace(/\/+$/, '');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const res = await fetch(clean + '/api/auth/login', {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    let body: any = {};
    try { body = await res.json(); } catch { /* non-JSON error page */ }
    if (!res.ok) {
      throw new ApiError(body.detail ?? `HTTP ${res.status}`, res.status);
    }
    return body as Session;
  } catch (err: any) {
    if (err instanceof ApiError) throw err;
    if (err?.name === 'AbortError') throw new ApiError('Server did not respond in time');
    throw new ApiError('Cannot reach the server — check the address is up on HTTPS');
  } finally {
    clearTimeout(timer);
  }
}

export const whoami = () =>
  request<{ user: string; expires_at: number | null; kind: string }>('/api/auth/me');

export const logoutEverywhere = () =>
  request<{ ok: boolean; epoch: number }>('/api/auth/logout-everywhere', { method: 'POST' });

// ------------------------------------------------------------- health & control

export const health = () =>
  request<{
    ok: boolean; server_time: string; timezone: string;
    auth_configured: boolean; login_available: boolean;
  }>('/api/health');

export const status = () => request<BotStatus>('/api/status');

export const startBot = (force = false) =>
  request<{ ok: boolean; pid?: number; reason?: string }>('/api/bot/start', {
    method: 'POST',
    body: JSON.stringify({ force }),
  });

export const stopBot = (reason = 'manual stop from app') =>
  request<{ ok: boolean; reason?: string }>('/api/bot/stop', {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });

export const restartBot = () => request<{ ok: boolean }>('/api/bot/restart', { method: 'POST' });

export interface ScheduleInfo {
  start: string; stop: string; timezone: string;
  auto: boolean; skip_holidays: boolean;
  enabled: boolean; next_start: string | null; next_stop: string | null;
}

export const getSchedule = () => request<ScheduleInfo>('/api/schedule');

export const setSchedule = (body: { enabled?: boolean; start?: string; stop?: string }) =>
  request<ScheduleInfo>('/api/schedule', { method: 'POST', body: JSON.stringify(body) });

// ------------------------------------------------------------- data

export const events = (sinceId = 0, limit = 200) =>
  request<{ events: FeedEvent[]; last_id: number }>(
    `/api/events?since_id=${sinceId}&limit=${limit}`,
  );

export const today = () =>
  request<{
    date: string; snapshot: any; status: BotStatus;
    trades: Trade[]; session: SessionRow | null;
    equity_marks: EquityMark[]; tail: FeedEvent[];
  }>('/api/today');

const rangeQuery = (period: Period, anchor: string, start?: string, end?: string) =>
  period === 'custom' && start && end
    ? `period=custom&start=${start}&end=${end}`
    : `period=${period}&anchor=${anchor}`;

export const trades = (period: Period, anchor: string, start?: string, end?: string) =>
  request<{ range: DateRange; count: number; trades: Trade[] }>(
    `/api/trades?${rangeQuery(period, anchor, start, end)}`,
  );

export const summary = (period: Period, anchor: string, start?: string, end?: string) =>
  request<{
    range: DateRange; summary: Summary;
    curve: { session_date: string; equity: number; day_pnl: number }[];
    sessions: SessionRow[];
  }>(`/api/summary?${rangeQuery(period, anchor, start, end)}`);

export const intradayEquity = (sessionDate?: string) =>
  request<{ date: string; marks: EquityMark[] }>(
    `/api/equity/intraday${sessionDate ? `?session_date=${sessionDate}` : ''}`,
  );

export const exportPreview = (period: Period, anchor: string) =>
  request<{ range: DateRange; summary: Summary; sessions: number; trades: number }>(
    `/api/export/preview?${rangeQuery(period, anchor)}`,
  );

// ------------------------------------------------------------- chart

export const chart = () => request<ChartData>('/api/chart');

// ------------------------------------------------------------- strategy

export const getStrategy = () => request<StrategyDescription>('/api/strategy');

export const putStrategy = (values: Record<string, any>) =>
  request<StrategyDescription>('/api/strategy', {
    method: 'PUT',
    body: JSON.stringify({ values }),
  });

export const resetStrategy = () =>
  request<StrategyDescription>('/api/strategy/reset', { method: 'POST' });

export const saveProfile = (name: string) =>
  request<StrategyDescription>('/api/strategy/profiles', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });

export const loadProfile = (name: string) =>
  request<StrategyDescription>('/api/strategy/profiles/load', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });

export const deleteProfile = (name: string) =>
  request<StrategyDescription>(`/api/strategy/profiles?name=${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });

// ------------------------------------------------------------- push

export const registerPush = (token: string, platform: string) =>
  request<{ ok: boolean }>('/api/push/register', {
    method: 'POST',
    body: JSON.stringify({ token, platform }),
  });

export const testPush = () => request<{ ok: boolean; tokens: number }>('/api/push/test', {
  method: 'POST',
});

// ------------------------------------------------------------- downloads

export function exportUrl(
  period: Period, anchor: string, format: Format,
  opts: { includeEvents?: boolean; start?: string; end?: string } = {},
): string {
  const p = new URLSearchParams({ format, token: TOKEN });
  if (period === 'custom' && opts.start && opts.end) {
    p.set('period', 'custom');
    p.set('start', opts.start);
    p.set('end', opts.end);
  } else {
    p.set('period', period);
    p.set('anchor', anchor);
  }
  if (opts.includeEvents) p.set('events', 'true');
  return `${BASE}/api/export?${p.toString()}`;
}

/**
 * Download an export and hand it to the OS share sheet, so it can be saved
 * to Files, mailed, or dropped into Drive — whatever the user wants.
 */
export async function downloadExport(
  period: Period, anchor: string, format: Format,
  opts: { includeEvents?: boolean; start?: string; end?: string; label?: string } = {},
): Promise<string> {
  const url = exportUrl(period, anchor, format, opts);
  const stem =
    period === 'custom' && opts.start && opts.end
      ? `${opts.start}_to_${opts.end}`
      : `${period}_${anchor}`;
  const target = `${FileSystem.cacheDirectory}meridian_${stem}.${format}`;

  const result = await FileSystem.downloadAsync(url, target);
  if (result.status !== 200) {
    throw new ApiError(`Download failed (HTTP ${result.status})`);
  }

  if (await Sharing.isAvailableAsync()) {
    await Sharing.shareAsync(result.uri, {
      mimeType:
        format === 'csv'
          ? 'text/csv'
          : format === 'json'
          ? 'application/json'
          : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      dialogTitle: opts.label ? `Trade results — ${opts.label}` : 'Trade results',
      UTI: format === 'csv' ? 'public.comma-separated-values-text' : undefined,
    });
  }
  return result.uri;
}

export function websocketUrl(): string {
  const ws = BASE.replace(/^http/, 'ws');
  return `${ws}/ws?token=${encodeURIComponent(TOKEN)}`;
}
