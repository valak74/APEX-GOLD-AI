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
