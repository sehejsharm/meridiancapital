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
    api: ApiStats | null;
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
    /** The engine's own last lines, kept only when it exited badly. */
    last_output?: string[];
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
  fleet: FleetOverview;
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
  algo_id?: string;
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
  algo_id?: string;
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

// ── fleet ────────────────────────────────────────────────────────────────────
export interface FleetAlgo {
  algo_id: string;
  name: string;
  kind: "builtin" | "uploaded";
  running: boolean;
  pid: number | null;
  mode: "paper" | "live";
}

export interface FleetOverview {
  algos: FleetAlgo[];
  running: number;
  live_running: number;
  total: number;
}

// ── algo registry ────────────────────────────────────────────────────────────
export interface GateCheck {
  key: string;
  title: string;
  spec: string;
  passed: boolean;
  detail: string;
  critical: boolean;
}

export interface GateReport {
  passed: boolean;
  error: string | null;
  total: number;
  failed: number;
  checks: GateCheck[];
  scan?: { ok: boolean; errors: string[]; imports: string[] };
  name?: string;
  /** Set for a standalone program: it runs as written, so nothing was gated. */
  program?: boolean;
  note?: string;
}

export interface AlgoVersion {
  id: number;
  algo_id: string;
  version: number;
  created_ts: string;
  uploaded_by: string | null;
  sha256: string;
  status: "pending" | "passed" | "failed" | "program";
  gate_report: string | null;
}

/** The gate's opinion of the active version. Advice, not a permission. */
export interface GateSummary {
  status: string;
  gate_passed: boolean;
  runnable: boolean;
}

export interface Algo {
  id: string;
  name: string;
  kind: "builtin" | "uploaded";
  mode: "paper" | "live";
  active_version: number | null;
  enabled: boolean;
  created_ts: string;
  notes: string | null;
  versions: AlgoVersion[];
  active: AlgoVersion | null;
  gate: GateSummary;
  /** How it runs: the built-in, a strategy module the engine calls, or a
   *  standalone program started as its own process. */
  runtime_kind?: "builtin" | "strategy" | "program" | "invalid" | "none";
  runtime: { running: boolean; pid: number | null; mode: string };
  /** Set when this registration is the paper twin of another algorithm. */
  shadow_of: string | null;
}

export interface AlgoList {
  algos: Algo[];
}

// ── deck feeds ───────────────────────────────────────────────────────────────
export interface HealthCheck {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  state: "ok" | "warning" | "critical" | "unknown";
  detail: string;
}

export interface HealthDetail {
  state: "ok" | "warning" | "critical" | "unknown";
  uptime_seconds: number;
  load_average: number[] | null;
  checks: HealthCheck[];
}

export interface NewsItem {
  source: string;
  title: string;
  link: string;
  summary: string;
  published: string | null;
}

export interface NewsPayload {
  items: NewsItem[];
  fetched_at: string | null;
  age_seconds: number;
  stale: boolean;
  sources: string[];
  errors: string[];
}

export interface TickerPayload {
  spot: number | null;
  bar_close: number | null;
  channel_high: number | null;
  channel_low: number | null;
  ts: string | null;
  source_algo: string | null;
  server_time: string;
  live: boolean;
}

// ── reports ──────────────────────────────────────────────────────────────────
export interface ReportSummary {
  trades: number;
  open_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  largest_win: number;
  largest_loss: number;
  average_trade: number;
  total_charges: number;
  max_drawdown_pct: number;
  errors: number;
}

export interface ReportPayload {
  title: string;
  algo_id: string;
  start: string;
  end: string;
  generated_at: string;
  summary: ReportSummary;
  trades: TradeRow[];
  equity: EquityPoint[];
  events: EventRow[];
}

// ── engine room ──────────────────────────────────────────────────────────────
export interface RateEndpoint {
  endpoint: string;
  cap_per_sec: number;
  rate_per_sec: number;
  utilisation: number;
  calls: number;
  throttled: number;
  cooling_off: boolean;
  account_calls_this_second: number | null;
}

export interface ApiStats {
  calls: Record<string, number>;
  total_calls: number;
  waited_sec: number;
  throttles: number;
  window_sec: number;
  shared_budget: boolean;
  shared_waited_sec: number;
  endpoints: RateEndpoint[];
  peak_utilisation: number;
}

export interface ShadowSide {
  trades: number;
  gross: number;
  charges: number;
  net: number;
  wins: number;
}

export interface ShadowSession {
  session_date: string;
  live: ShadowSide;
  paper: ShadowSide;
  drag: number;
  drag_pct_of_paper: number | null;
  both_traded: boolean;
}

export interface ShadowComparison {
  configured: boolean;
  live_algo_id: string;
  shadow_algo_id: string | null;
  sessions?: ShadowSession[];
  paired_sessions?: number;
  totals?: {
    live_net: number;
    paper_net: number;
    drag: number;
    drag_pct_of_paper: number | null;
    charges_paid: number;
    slippage_est: number;
    avg_drag_per_session: number | null;
  };
}

// ── market view ──────────────────────────────────────────────────────────────
export interface NiftyBar {
  /** IST wall-clock, as seconds since the epoch read as UTC. */
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface NiftyChartPayload {
  source: "angel" | "yahoo" | null;
  label: string | null;
  bars: NiftyBar[];
  age_seconds?: number | null;
  stale?: boolean;
  error?: string;
}

export interface ChainSide {
  tsym: string;
  token: string;
  ltp: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
  oi: number | null;
  bid: number | null;
  ask: number | null;
  high: number | null;
  low: number | null;
  /** Implied volatility, in percent. */
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  /** Rupees per unit per calendar day. */
  theta: number | null;
  /** Rupees per unit per one point of volatility. */
  vega: number | null;
}

export interface ChainRow {
  strike: number;
  ce: ChainSide | null;
  pe: ChainSide | null;
}

export interface ChainPayload {
  available: boolean;
  reason?: string;
  ts?: string;
  spot?: number;
  expiry?: string;
  dte?: number;
  atm?: number;
  lot?: number;
  highlight?: {
    tsym: string;
    token: string;
    source: "engine" | "account";
    qty?: number | null;
    strike?: number | null;
    right?: "CE" | "PE" | null;
    expiry?: string | null;
  } | null;
  rows?: ChainRow[];
  model?: string;
  age_seconds?: number | null;
  stale?: boolean;
}
