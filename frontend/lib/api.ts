export type Money = { amount: string; ccy: string };

export type Me = { id: number; email: string; role: "viewer" | "researcher" | "trader" | "admin"; mfa_enabled: boolean };

export type SystemStatus = {
  version: string;
  mode: "simulation" | "paper" | "live";
  broker: Record<string, unknown> & { name: string; connected: boolean };
  trading_enabled: boolean;
  kill_switch_engaged: boolean;
  kill_switch_reason: string | null;
  kill_switch_at: string | null;
  risk_config_hash: string;
  risk_config_version: number | null;
  market_data: {
    source: "none" | "ctrader";
    state: "disabled" | "not_configured" | "connecting" | "connected" | "reconnecting" | "error";
    error: string | null;
    broker: string | null;
    spec_source: string;
    last_quote_age_s: number | null;
  };
  llm_enabled: boolean;
};

export type LiveQuote = { symbol: string; bid: string; ask: string; spread: string; ts: string; age_s: number };

export type SpecOut = {
  source: string;
  spec: {
    contract_size: string;
    volume_min: string;
    volume_step: string;
    commission_type: string;
    commission_rate: string;
    commission_ccy: string;
    min_commission: string;
    margin_rate: string | null;
  };
  commission_per_lot_side_at_price: string | null;
};

export type Account = {
  mode: string;
  account_id: string;
  currency: string;
  balance: Money;
  equity: Money;
  margin_used: Money;
  free_margin: Money;
  leverage: number;
  margin_level_pct: string | null;
};

export type Position = {
  position_id: string;
  symbol: string;
  side: "buy" | "sell";
  volume: string;
  open_price: string;
  stop_loss: string | null;
  take_profit: string | null;
  opened_at: string;
  unrealized_pnl: Money | null;
};

export type RiskConfigOut = { version: number | null; config_hash: string; config: Record<string, string | number> };

export type SizingResult = {
  approved: boolean;
  rejections: string[];
  warnings: string[];
  volume: string;
  raw_volume: string;
  price_risk: string;
  loss_per_lot: Money | null;
  risk_budget: Money | null;
  risk_amount: Money | null;
  risk_pct_actual: string | null;
  margin_required: Money | null;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export function fmtMoney(m: Money | null | undefined, digits = 2): string {
  if (!m) return "—";
  const n = Number(m.amount);
  return `${n.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${m.ccy}`;
}

export type BarOut = {
  start: string;
  open: string;
  high: string;
  low: string;
  close: string;
  spread_avg: string;
  spread_max: string;
  tick_count: number;
  complete: boolean;
};

export type Quality = {
  symbol: string;
  score: number;
  tradeable: boolean;
  market_open: boolean;
  last_quote_age_s: number | null;
  spread: string | null;
  spread_median: string | null;
  issues: string[];
  counters: Record<string, number>;
};

export function fmtNum(v: string | number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || v === "") return "—";
  return Number(v).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export type TFAnalysis = {
  timeframe: string;
  role: "higher" | "intermediate" | "execution";
  bars: number;
  last_close: number | null;
  trend: "up" | "down" | "none";
  effective_trend: "up" | "down" | "none";
  regime: { label: string; direction: string; volatility: string; risk_multiplier: number; reasons: string[] };
  indicators: { atr: number | null; rsi: number | null; ema50: number | null; ema200: number | null };
  premium_discount: { zone: string; position: number; equilibrium: number } | null;
  last_event: { kind: string; direction: string; t: string; level: number } | null;
};

export type Context = {
  generated_at: string;
  bias: "bullish" | "bearish" | "neutral";
  bias_score: number;
  alignment: string;
  htf_conflict: boolean;
  htf_available: boolean;
  summary: string[];
  timeframes: Record<string, TFAnalysis>;
  levels: { name: string; price: number; kind: string; complete: boolean }[];
};

export type Overlays = {
  events: { kind: "BOS" | "CHoCH"; direction: "bullish" | "bearish"; t: string; level: number; score: number }[];
  fvgs: { direction: string; top: number; bottom: number; t: string }[];
  levels: { name: string; price: number; kind: string; complete: boolean }[];
};

export type Lockout = {
  locked: boolean;
  reason: string | null;
  event_title: string | null;
  until: string | null;
  next_event_title: string | null;
  next_event_at: string | null;
  before_min: number;
  after_min: number;
};

export type CalEvent = {
  event_code: string;
  title: string;
  currency: string;
  importance: "low" | "medium" | "high";
  scheduled_at: string;
  previous: string | null;
  forecast: string | null;
  actual: string | null;
};

export const REGIME_FR: Record<string, string> = {
  strong_trend: "Tendance forte",
  weak_trend: "Tendance faible",
  range: "Range",
  breakout: "Cassure",
  transition: "Transition",
  insufficient_data: "Données insuffisantes",
};

export function fmtTime(iso: string | null, withDay = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("fr-FR", withDay ? { weekday: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
}

export type Confluence = { key: string; label: string; ok: boolean; weight: number; detail: string };

export type Narrative = { resume: string; pourquoi: string[]; risques: string[]; invalidation: string; prudence: string; model: string };

export type Opportunity = {
  id: string;
  setup: string;
  setup_label: string;
  direction: "bullish" | "bearish";
  timeframe: string;
  detected_at: string;
  entry: number;
  stop: number;
  targets: number[];
  risk_reward: number;
  quality: number;
  confluences: Confluence[];
  warnings: string[];
  blockers: string[];
  invalidation: string;
  evidence: {
    sweep?: { level: number; extreme: number; t: string };
    zone?: { kind: string; top: number; bottom: number; t: string };
    trigger_bar?: string;
  };
  stats: {
    label: string;
    n: number;
    hit_rate: number | null;
    hit_rate_ci: [number, number] | null;
    small_sample: boolean;
    breakeven_rate: number | null;
  } | null;
  status: "active" | "target_reached" | "invalidated" | "expired";
  narrative: Narrative | null;
};

export type Indicators = { t: string[]; ema50: (number | null)[]; ema200: (number | null)[]; rsi: (number | null)[] };

export const STATUS_FR: Record<Opportunity["status"], string> = {
  active: "Active",
  target_reached: "Objectif atteint",
  invalidated: "Invalidée",
  expired: "Expirée",
};

export type RiskStatus = {
  currency: string;
  entry_allowed: boolean;
  blocks: string[];
  realized_today: string;
  realized_week: string;
  daily_loss: string;
  daily_cap: string;
  remaining_daily: string;
  weekly_loss: string;
  weekly_cap: string;
  drawdown: string;
  max_drawdown: string;
  trades_today: number;
  max_trades: number;
  consecutive_losses: number;
  max_consecutive_losses: number;
  open_positions: number;
  max_open_positions: number;
  regime_multiplier: string;
};

export type RiskCheck = { code: string; label: string; passed: boolean; value: string; limit: string; severity: string };

export type RiskDecision = {
  proposal_id: string;
  approved: boolean;
  decided_at: string;
  checks: RiskCheck[];
  rejections: string[];
  critical: boolean;
  risk_pct_effective: string;
  reductions: string[];
  net_risk_reward: string | null;
  sizing: { volume: string; risk_amount: Money | null; margin_required: Money | null } | null;
  order: { volume: string; stop_loss: string; take_profit: string; client_order_id: string } | null;
};

// --------------------------------------------------------------------------- backtests
export type Setup = "liquidity_sweep_reversal" | "trend_pullback";
export const SETUP_FR: Record<Setup, string> = {
  liquidity_sweep_reversal: "Balayage de liquidité",
  trend_pullback: "Repli dans la tendance",
};

export type BacktestRequest = {
  setup: Setup;
  timeframe: "5m" | "15m" | "1h";
  start: string | null;
  end: string | null;
  stop_buffer_atr: number;
  min_quality: number;
  require_htf_alignment: boolean;
  max_hold_bars: number;
  sessions: ("asia" | "london" | "newyork")[];
  spread_mult: number;
  slippage: number;
  swap_per_lot_night: number;
  initial_balance: number;
  final_holdout?: boolean;
};

export type Group = { n: number; avg_r: number; win_rate: number };

export type BTSummary = {
  trades: number;
  net_profit?: number;
  return_pct?: number;
  win_rate?: number;
  win_rate_ci?: [number, number];
  profit_factor?: number | null;
  expectancy_r?: number;
  expectancy_r_ci?: [number, number];
  avg_win_r?: number | null;
  avg_loss_r?: number | null;
  max_drawdown?: number;
  max_drawdown_pct?: number;
  recovery_factor?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  avg_bars_held?: number;
  max_win_streak?: number;
  max_loss_streak?: number;
  total_costs?: number;
  monthly?: Record<string, number>;
  by?: Record<"session" | "regime" | "direction" | "exit", Record<string, Group>>;
  from?: string;
  to?: string;
};

export type BTTrade = {
  signal_t: string; entry_t: string; exit_t: string; direction: "bullish" | "bearish"; setup: string; regime: string;
  quality: number; volume: number; entry: number; stop: number; target: number; exit: number; reason: string;
  gross: number; costs: number; net: number; risk_amount: number; r: number; mfe_r: number; mae_r: number; bars_held: number;
};

export type GateItem = { name: string; passed: boolean; value: string; requirement: string };

export type BTReport = {
  data: { bars: number; timeframe: string; start: string; end: string; sources: string[]; news_windows: number; holdout_start?: string | null; final_holdout?: boolean };
  params: Record<string, unknown>;
  costs: Record<string, number>;
  assumptions: Record<string, string>;
  spec: { source: string; commission: string; contract_size: string };
  risk_config_hash: string;
  signals: number;
  rejected_by_risk_engine: Record<string, number>;
  summary: BTSummary;
  stress_costs_150: BTSummary;
  folds: BTSummary[];
  walk_forward: {
    periods: { period: number; params: { stop_buffer_atr: number; min_quality: number } | null; reason?: string; train_expectancy_r?: number; test_trades?: number }[];
    oos: BTSummary;
  };
  sensitivity: { stop_buffer_atr: number; min_quality: number; trades: number; expectancy_r: number | null; profit_factor: number | null }[];
  monte_carlo: { runs: number; max_dd_median_pct?: number; max_dd_p95_pct?: number; prob_dd_over_pct?: number; prob_dd_over?: number };
  validation: { validated: boolean; items: GateItem[] };
  equity: [string, number][];
  drawdown: [string, number][];
  trades: BTTrade[];
};

export type BacktestListItem = {
  id: number; created_at: string; created_by: string; status: "running" | "done" | "error"; error: string | null;
  request: BacktestRequest; trades: number | null; expectancy_r: number | null; profit_factor: number | null;
  validated: boolean | null; data: BTReport["data"] | null;
};

export type BacktestDetail = {
  id: number; status: "running" | "done" | "error"; error: string | null; request: BacktestRequest;
  report: BTReport | null; created_at: string; finished_at: string | null;
};

/** Backend datetimes may come as "YYYY-MM-DD HH:MM:SS+00:00" (report JSON) — normalise for Date. */
export function toDate(s: string): Date {
  return new Date(s.includes("T") ? s : s.replace(" ", "T"));
}

// --------------------------------------------------------------------------- research
export type Holdout = { start: string | null; created_at: string | null; note: string | null; consultations: number };

export type CompRow = {
  timeframe: string; component: string; condition_key: string; condition: string; horizon: number; n: number; events: number;
  mean_excess_atr: number; ci: [number | null, number | null]; hit_rate: number; p: number; halves: [number | null, number | null];
  significant: boolean; exceeds_cost: boolean; stable: boolean; retained: boolean;
};

export type ExitRow = {
  timeframe: string; setup: Setup; rule: string; n: number; expectancy_r?: number; ci?: [number | null, number | null];
  win_rate?: number; p?: number; halves?: [number | null, number | null]; significant?: boolean; stable?: boolean; retained?: boolean;
};

export type CostRow = {
  timeframe: string; bars: number; median_atr?: number; median_spread?: number; commission_rt?: number; slippage_rt?: number;
  roundtrip_cost?: number; cost_pct_atr?: number; skipped?: string;
};

export type DiagReport = {
  costs: CostRow[];
  event_counts: Record<string, Record<string, number>>;
  components: CompRow[];
  exits: ExitRow[];
  tests_total: number;
  significant_total: number;
  retained_total: number;
  edges_total: number;
  fdr: number;
  findings: string[];
  labels: { components: Record<string, string>; exits: Record<string, string> };
  method: Record<string, string>;
  data: { bars_5m: number; start: string; end: string; sources: string[]; holdout_start: string | null; holdout_note: string; previous_diagnostics: number };
};

export type DiagListItem = {
  id: number; created_at: string; created_by: string; status: "running" | "done" | "error"; error: string | null;
  request: { timeframes: string[] }; tests_total: number | null; retained_total: number | null; edges_total: number | null; data: DiagReport["data"] | null;
};

export type DiagDetail = { id: number; status: "running" | "done" | "error"; error: string | null; report: DiagReport | null; created_at: string };
