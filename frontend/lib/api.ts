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
  market_data: string;
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
