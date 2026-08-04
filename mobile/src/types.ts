export type Period = 'day' | 'week' | 'month' | 'quarter' | 'year' | 'all' | 'custom';
export type Format = 'csv' | 'xlsx' | 'json';

export interface MarketSnapshot {
  adx: number; atr: number; garch: number; vol_regime: string;
  trend: string; direction: string; vwap_side: string;
  ser: number; efficiency: string; spot: number; vwap: number;
  ema9: number; ema21: number; slope: number;
  day_range_pts: number; day_range_pct: number; day_move: number;
}

export interface LadderTrigger {
  label: string;
  target_pct?: number | null;
  target_price?: number;
  gain_pct: number;
  progress: number;
}

export interface Position {
  symbol: string; opt_type: 'C' | 'P'; strike: number; expiry?: string;
  qty: number; stage: 'INIT' | 'BE' | 'LOCK1' | 'LOCK2' | 'FREE';
  entry: number; current: number; pnl: number; pnl_pct: number;
  sl_price: number; sl_pct: number; high_prem: number; peak_gain_pct: number;
  risk_left: number; entry_time: string; entry_reason: string;
  model_prem?: number | null; entry_iv?: number | null;
  lot_cost?: number | null; real_margin?: number | null;
  next_trigger?: LadderTrigger;
  levels?: LadderLevels;
  token?: string;
}

export interface Decision {
  action: 'C' | 'P' | null;
  reason: string;
  detail: string;
}

export interface LatencyStat {
  n: number; avg: number; mn: number; mx: number; p95: number; last: number;
}

export interface Snapshot {
  time?: string;
  market?: MarketSnapshot;
  decision?: Decision;
  position?: Position | null;
  equity?: number; open_equity?: number; day_pnl?: number; unrealised?: number;
  trades?: number; max_trades?: number;
  killed?: boolean; chop_skip?: boolean;
  chop_score?: number | null; chop_cutoff?: number | null;
  kill_used?: number; kill_limit?: number;
  peak_equity?: number; drawdown_pct?: number; total_return_pct?: number;
  seed_capital?: number; sessions_run?: number;
  latency?: Record<string, LatencyStat | null>;
  lot_size?: number; paper?: boolean;
  window?: { open: string; cutoff: string; force_close: string; end: string };
  reject_counts?: Record<string, number>;
  _ts?: string;
}

export interface BotStatus {
  state: 'stopped' | 'starting' | 'running' | 'stopping' | 'error';
  running: boolean;
  pid: number | null;
  started_at: string | null;
  uptime_seconds: number;
  session_date: string;
  restarts: number;
  stop_reason: string | null;
  last_error: string | null;
  server_time: string;
  timezone: string;
  is_trading_day: boolean;
  not_trading_reason: string | null;
  inside_window: boolean;
  schedule: { start: string; stop: string; auto: boolean };
  snapshot: Snapshot;
  last_event?: FeedEvent;
  schedule_next?: { enabled: boolean; next_start: string | null; next_stop: string | null };
  config?: {
    paper_mode: boolean; seed_capital: number; paper_slippage_pct: number;
    session_start: string; session_stop: string; tz: string;
    auto_schedule: boolean; skip_holidays: boolean;
    client_id: string; credentials_present: boolean; push_enabled: boolean;
  };
}

export interface FeedEvent {
  id?: number;
  ts: string;
  session_date: string;
  kind: string;
  level: 'info' | 'warn' | 'error' | 'success' | 'debug';
  message: string;
  payload?: Record<string, any> | null;
  seq?: number;
}

export interface Trade {
  id: number; session_date: string; mode: string;
  entry_time: string; exit_time: string; hold_min: number;
  symbol: string; opt_type: string; strike: number; qty: number;
  avg_entry: number; exit_fill: number;
  gross_pnl: number; charges: number; net_pnl: number;
  brokerage: number; stt: number; exch_txn: number; sebi: number;
  stamp: number; gst: number;
  reason: string; stage: string; entry_reason: string;
  spot_at_entry: number; garch_vol: number; entry_iv: number | null;
  model_prem: number | null; lot_cost: number; real_margin: number;
  risk_rs: number; day_pnl: number; equity_after: number;
  latency_ms: number | null;
}

export interface SessionRow {
  session_date: string; mode: string; trades: number;
  open_equity: number; close_equity: number; day_pnl: number; charges: number;
  killed: number; chop_blocked: number; chop_score: number | null;
  garch: number | null; adx: number | null;
  vol_regime: string | null; trend: string | null;
  direction: string | null; efficiency: string | null;
  day_range_pts: number | null; avg_latency_ms: number | null;
  peak_equity: number | null; drawdown_pct: number | null;
  win_rate: number | null; profit_factor: number | null;
}

export interface Summary {
  start: string; end: string;
  sessions: number; trading_days: number;
  trades: number; wins: number; losses: number;
  win_rate: number; gross_profit: number; gross_loss: number;
  profit_factor: number | null; net_pnl: number; charges: number;
  avg_trade: number; best_trade: number; worst_trade: number;
  avg_hold_min: number;
  open_equity: number | null; close_equity: number | null;
  peak_equity: number | null; max_drawdown_pct: number;
  return_pct: number; days_blocked_chop: number; days_killed: number;
}

export interface DateRange { start: string; end: string; label: string }

/** [timestamp_ms, open, high, low, close, volume] */
export type Candle = [number, number, number, number, number, number];

export interface LadderLevel {
  price: number;
  pct: number;
  floor?: number;
  label: string;
}

export interface LadderLevels {
  stop: LadderLevel;
  breakeven: LadderLevel;
  lock1: LadderLevel;
  lock2: LadderLevel;
  free: { giveback_pct: number; label: string };
}

export interface OptionTrack {
  symbol: string;
  opt_type: 'C' | 'P';
  strike: number;
  /** [timestamp_ms, premium] */
  series: [number, number][];
  entry: number;
  sl: number;
  stage: string;
  high_prem: number;
  entry_ts: number | null;
  levels: LadderLevels;
}

export interface ChartData {
  available: boolean;
  reason?: string;
  index?: string;
  source?: string;
  interval?: string;
  spot?: number | null;
  candles: Candle[];
  vwap?: (number | null)[];
  ema9?: (number | null)[];
  ema21?: (number | null)[];
  option: OptionTrack | null;
  _ts?: string;
}

export interface StrategyParam {
  key: string;
  label: string;
  type: 'pct' | 'rupees' | 'int' | 'bool' | 'time' | 'text';
  value: any;
  default: any;
  min: number | null;
  max: number | null;
  step: number | null;
  help: string;
  critical: boolean;
  modified: boolean;
}

export interface StrategyGroup {
  name: string;
  params: StrategyParam[];
}

export interface StrategyProfile {
  name: string;
  saved_at: string | null;
  changes: number;
}

export interface StrategyDescription {
  groups: StrategyGroup[];
  drift: Record<string, {
    label: string; group: string; v11: any; current: any; critical: boolean;
  }>;
  problems: string[];
  profiles: StrategyProfile[];
  applies_at: string;
  bot_running: boolean;
}

export interface EquityMark {
  ts: string; session_date: string; equity: number;
  day_pnl: number; open_position: number; unrealised: number;
}
