"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { KillSwitch } from "@/components/KillSwitch";
import { LivePrice } from "@/components/LivePrice";
import { ModeBanner } from "@/components/ModeBanner";
import { Panel, Pending, Stat } from "@/components/Panel";
import { SizeCalculator } from "@/components/SizeCalculator";
import { api, ApiError, fmtMoney, type Account, type LiveQuote, type Me, type Position, type RiskConfigOut, type SpecOut, type SystemStatus } from "@/lib/api";

const RISK_LABELS: Record<string, [string, "pct" | "int" | "usd" | "lot" | "min"]> = {
  risk_per_trade_pct: ["Risque par trade", "pct"],
  max_daily_loss_pct: ["Perte max / jour", "pct"],
  max_weekly_loss_pct: ["Perte max / semaine", "pct"],
  max_total_drawdown_pct: ["Drawdown total max", "pct"],
  max_open_positions: ["Positions ouvertes max", "int"],
  max_consecutive_losses: ["Pertes consécutives max", "int"],
  max_trades_per_day: ["Trades max / jour", "int"],
  max_spread: ["Spread max", "usd"],
  max_slippage: ["Slippage max", "usd"],
  max_volume_lots: ["Volume max", "lot"],
  news_lockout_minutes_before: ["Blocage avant news", "min"],
  news_lockout_minutes_after: ["Blocage après news", "min"],
};

const MD_STATE_FR: Record<SystemStatus["market_data"]["state"], string> = {
  disabled: "désactivée",
  not_configured: "non configurée",
  connecting: "connexion…",
  connected: "connectée",
  reconnecting: "reconnexion…",
  error: "erreur",
};

function fmtRisk(v: string | number, kind: string): string {
  if (kind === "pct") return `${(Number(v) * 100).toFixed(2)} %`;
  if (kind === "usd") return `${v} $`;
  if (kind === "lot") return `${v} lot`;
  if (kind === "min") return `${v} min`;
  return String(v);
}

export default function Dashboard() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [risk, setRisk] = useState<RiskConfigOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [quote, setQuote] = useState<LiveQuote | null>(null);
  const [spec, setSpec] = useState<SpecOut | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, s, a, p, r] = await Promise.all([
        api<Me>("/auth/me"),
        api<SystemStatus>("/system/status"),
        api<Account>("/account"),
        api<Position[]>("/positions"),
        api<RiskConfigOut>("/risk/config"),
      ]);
      setMe(m); setStatus(s); setAccount(a); setPositions(p); setRisk(r); setError(null);
      setSpec(await api<SpecOut>("/market/spec"));
      setQuote(await api<LiveQuote>("/market/quote").catch(() => null));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Erreur inconnue");
    }
  }, [router]);

  useEffect(() => {
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [load]);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (!status || !account || !me) {
    return <main className="p-8 text-muted">{error ? `Backend indisponible : ${error}` : "Chargement…"}</main>;
  }

  const pnl = Number(account.equity.amount) - Number(account.balance.amount);
  const canTrade = me.role === "trader" || me.role === "admin";

  return (
    <div className="min-h-screen">
      <ModeBanner mode={status.mode} />
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-6">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-semibold tracking-wide"><span className="text-gold">APEX</span> GOLD AI</h1>
          <span className="text-xs text-muted">v{status.version}</span>
        </div>
        <div className="flex items-center gap-3">
          <KillSwitch engaged={status.kill_switch_engaged} canUse={canTrade} onDone={load} />
          <span className="hidden text-sm text-muted sm:inline">{me.email} · {me.role}</span>
          <button onClick={logout} className="text-sm text-muted hover:text-text">Déconnexion</button>
        </div>
      </header>

      {error && <p className="bg-down/20 px-6 py-2 text-sm text-down">Erreur de rafraîchissement : {error}</p>}
      {status.kill_switch_engaged && (
        <p className="bg-down/15 px-6 py-2 text-sm text-down">
          Arrêt d&apos;urgence engagé ({status.kill_switch_reason}). Trading bloqué jusqu&apos;au réarmement par un admin.
        </p>
      )}

      <main className="mx-auto grid max-w-7xl gap-4 p-4 sm:p-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Panel title={`Compte · ${account.account_id}`}>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <Stat label="Solde" value={fmtMoney(account.balance)} />
              <Stat label="Équité" value={fmtMoney(account.equity)} />
              <Stat label="P&L latent" value={fmtMoney({ amount: String(pnl), ccy: account.currency })} tone={pnl > 0 ? "up" : pnl < 0 ? "down" : undefined} />
              <Stat label="Marge libre" value={fmtMoney(account.free_margin)} />
              <Stat label="Marge utilisée" value={fmtMoney(account.margin_used)} />
              <Stat label="Niveau de marge" value={account.margin_level_pct ? `${Number(account.margin_level_pct).toFixed(0)} %` : "—"} />
            </div>
          </Panel>

          <Panel title="Positions ouvertes">
            {positions.length === 0 ? (
              <p className="text-sm text-muted">Aucune position.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="num w-full text-sm">
                  <thead className="text-left text-xs text-muted">
                    <tr><th className="py-1">Symbole</th><th>Sens</th><th>Volume</th><th>Entrée</th><th>SL</th><th>TP</th><th className="text-right">P&L</th></tr>
                  </thead>
                  <tbody>
                    {positions.map((p) => (
                      <tr key={p.position_id} className="border-t border-line">
                        <td className="py-1.5">{p.symbol}</td>
                        <td className={p.side === "buy" ? "text-up" : "text-down"}>{p.side === "buy" ? "Achat" : "Vente"}</td>
                        <td>{p.volume}</td><td>{p.open_price}</td><td>{p.stop_loss ?? "—"}</td><td>{p.take_profit ?? "—"}</td>
                        <td className="text-right">{fmtMoney(p.unrealized_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel title="Calculateur de taille de position · XAUUSD">
            <SizeCalculator accountCcy={account.currency} />
          </Panel>

          <Panel title="XAU/USD en direct">
            <LivePrice md={status.market_data} quote={quote} spec={spec} />
          </Panel>

          <Panel title="Graphique XAU/USD"><Pending phase={3} what="Bougies et historique" /></Panel>
        </div>

        <div className="space-y-4">
          <Panel title="État du système">
            <dl className="space-y-1.5 text-sm">
              <Row k="Mode" v={status.mode.toUpperCase()} />
              <Row k="Broker" v={`${status.broker.name} · ${status.broker.connected ? "connecté" : "déconnecté"}`} ok={status.broker.connected} />
              <Row
                k="Données de marché"
                v={status.market_data.source === "none" ? "aucune source" : `${status.market_data.source} · ${MD_STATE_FR[status.market_data.state]}`}
                ok={status.market_data.state === "connected"}
              />
              <Row k="Trading autorisé" v={status.trading_enabled ? "oui" : "non"} ok={status.trading_enabled} />
              <Row k="Arrêt d'urgence" v={status.kill_switch_engaged ? "ENGAGÉ" : "non"} ok={!status.kill_switch_engaged} />
              <Row k="2FA" v={me.mfa_enabled ? "activée" : "non activée"} ok={me.mfa_enabled} />
            </dl>
          </Panel>

          {risk && (
            <Panel title="Limites de risque" right={<span className="num text-xs text-muted">{risk.version ? `v${risk.version}` : "défaut"} · {risk.config_hash.slice(0, 8)}</span>}>
              <dl className="space-y-1 text-sm">
                {Object.entries(RISK_LABELS).map(([key, [label, kind]]) =>
                  key in risk.config ? <Row key={key} k={label} v={fmtRisk(risk.config[key], kind)} /> : null,
                )}
              </dl>
              <p className="mt-3 text-xs text-muted">Modifiable uniquement par un admin avec mot de passe + 2FA. Jamais par l&apos;IA.</p>
            </Panel>
          )}

          <Panel title="Analyse IA"><Pending phase={8} what="Régime, scores et décision" /></Panel>
          <Panel title="News & calendrier"><Pending phase={4} what="Flux de news et calendrier économique" /></Panel>
        </div>
      </main>
    </div>
  );
}

function Row({ k, v, ok }: { k: string; v: string; ok?: boolean }) {
  const color = ok === undefined ? "" : ok ? "text-up" : "text-paper";
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted">{k}</dt>
      <dd className={`num text-right ${color}`}>{v}</dd>
    </div>
  );
}
