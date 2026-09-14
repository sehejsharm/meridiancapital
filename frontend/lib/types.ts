/** Mirrors the snapshot contract published by the engine (engine/runner.py). */

export type EnginePhase =
  | "MARKET CLOSED"
  | "IN POSITION"
  | "HALTED"
  | "SCANNING"
  | "PRE-ENTRY WINDOW"
  | "ENTRY WINDOW CLOSED";

export type TradingMode = "paper" | "live";

export type EventLevel = "debug" | "info" | "ok" | "warn" | "error" | "critical";

export interface Snapshot {
  ts: string;
  engine: {
    version: string;
    mode: TradingMode;
    phase: EnginePhase;
    pid: number;
    uptime_sec: number;
    banner: string;
    build: string;
  };
  market: {
    open: boolean;
    session_date: string;
    entry_window: {
      start: string;
      cutoff: string;
      force_close: string;
      open_now: boolean;
    };
  };
  account: {
    equity: number;
    start_equity: number;
    peak_equity: number;
    week_start_equity: number;
    day_pl: number;
    day_pl_pct: number;
    realised_today: number;
    realised_week: number;
    drawdown_pct: number;
    currency: string;
  };
  signal: {
    spot: number | null;
    channel_high: number | null;
    channel_low: number | null;
    lookback: number;
    view: string;
    state: "break_up" | "break_down" | "inside" | "unknown";
    room_up: number | null;
    room_down: number | null;
    bar_close: number | null;
    bar_age_sec: number | null;
    bars_loaded: number;
    divergence_pts: number | null;
    divergence_limit: number;
    next_strike: number | null;
  };
  position: Position | null;
  guards: Guards;
  health: {
    clock_drift_sec: number | null;
    broker_connected: boolean;
    broker_client_id: string | null;
    api: { total_calls?: number; throttles?: number; waited_sec?: number } | null;
    contracts_loaded: number;
    telemetry_dropped: number;
    last_error: string | null;
  };
}

export interface Position {
  tsym: string;
  side: "CE" | "PE";
  strike: number;
  expiry: string;
  view: string;
  lots: number;
  qty: number;
  entry_premium: number;
  live_premium: number | null;
  gain_pct: number | null;
  peak_pct: number;
  unrealised: number | null;
  notional: number;
  stop_price: number;
  stop_pct: number;
  stop_state: string;
  target_pts: number;
  index_move_pts: number | null;
  spot_entry: number;
  opened_ts: string;
  hold_min: number;
}

export interface Guards {
  halted: boolean;
  week_halted: boolean;
  locked_profit: boolean;
  trades_today: number;
  max_trades_day: number;
  consec_losses: number;
  consec_loss_halt: number;
  daily_loss_used: number;
  daily_loss_limit: number;
  weekly_loss_used: number;
  weekly_loss_limit: number;
  profit_lock_progress: number;
  profit_lock_target: number;
  drawdown_stop: number;
  min_capital: number;
  capital_ok: boolean;
  deploy_fraction: number;
  per_trade_equity_cap: number;
  per_trade_risk_rs: number;
  max_lots: number;
}

export interface SystemStatus {
  engine: {
    running: boolean;
    pid: number | null;
    mode: TradingMode;
    adopted?: boolean;
    uptime_sec?: number | null;
    last_exit_code?: number | null;
    last_stop_reason?: string;
    restarts_this_session?: number;
    restart_backoff_remaining?: number;
    manual_override?: boolean;
    last_start_error?: string;
  };
  schedule: {
    enabled: boolean;
    now_ist: string;
    is_trading_day: boolean;
    in_session_window: boolean;
    next: { action: "start" | "stop" | "none"; at: string | null; day: string | null };
    holiday_count: number;
    calendar_configured: boolean;
    last_tick: string | null;
    last_decision: string;
    manual_override: boolean;
    max_restarts_per_session: number;
  };
  offline_note: { ts: string; reason: string; mode: string } | null;
  operator: string;
  fund: string;
}

export interface TradeRow {
  id: number;
  entry_ts: string;
  exit_ts: string | null;
  session_date: string;
  mode: TradingMode;
  view: string | null;
  side: "CE" | "PE" | null;
  strike: number | null;
  expiry: string | null;
  tsym: string | null;
  lots: number | null;
  qty: number | null;
  entry_prem: number | null;
  exit_prem: number | null;
  spot_entry: number | null;
  spot_exit: number | null;
  peak_pct: number | null;
  gross: number | null;
  charges: number | null;
  net: number | null;
  reason: string | null;
  hold_min: number | null;
  equity: number | null;
  pnl_source: "angel" | "estimate" | null;
}

export interface TradeStats {
  n: number;
  wins: number;
  losses: number;
  win_rate: number;
  net: number;
  gross: number;
  charges: number;
  best: number;
  worst: number;
  avg_hold: number;
}

export interface EventRow {
  id: number;
  ts: string;
  level: EventLevel;
  source: string;
  message: string;
  extra: Record<string, unknown> | null;
}

export interface EquityPoint {
  ts: string;
  equity: number;
  realised_today: number | null;
  peak_equity: number | null;
  day_pl: number | null;
}

export interface DailyEquityPoint {
  session_date: string;
  equity: number;
  day_pl: number | null;
}

export interface Holiday {
  day: string;
  label: string;
}

export interface CommandRow {
  id: number;
  created_ts: string;
  action: string;
  issued_by: string | null;
  status: string;
  result: string | null;
}

export interface LiveState {
  snapshot: Snapshot | null;
  status: SystemStatus | null;
  events: EventRow[];
}

export type TuningKind = "int" | "float" | "pct" | "money" | "time";

export interface TuningParam {
  key: string;
  group: string;
  label: string;
  kind: TuningKind;
  default: number | string;
  min: number;
  max: number;
  step: number;
  help: string;
  riskier_up: boolean;
}

export interface TuningPayload {
  params: TuningParam[];
  groups: string[];
  defaults: Record<string, number | string>;
  overrides: Record<string, number | string>;
  effective: Record<string, number | string>;
  changed: string[];
  riskier: string[];
  engine_running: boolean;
  pending_restart: boolean;
  confirm_phrase: string;
}
