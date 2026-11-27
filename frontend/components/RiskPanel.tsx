"use client";

import { useState } from "react";
import { api, fmtNum, type Me, type RiskConfigOut, type RiskStatus } from "@/lib/api";
import { Badge, Card } from "./ui";

function Meter({ label, used, cap, fmt }: { label: string; used: number; cap: number; fmt: (x: number) => string }) {
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  const color = pct >= 100 ? "bg-danger" : pct >= 60 ? "bg-warn" : "bg-gold";
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="num">{fmt(used)} <span className="text-faint">/ {fmt(cap)}</span></span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-elev" role="meter" aria-label={label} aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function RiskPanel({ risk, cfg, me, tradingEnabled, killSwitch, onChange }: {
  risk: RiskStatus | null;
  cfg: RiskConfigOut | null;
  me: Me;
  tradingEnabled: boolean;
  killSwitch: boolean;
  onChange: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [totp, setTotp] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const isAdmin = me.role === "admin";
  const isTrader = me.role === "trader" || isAdmin;
  const money = (x: number) => `${fmtNum(x)} ${risk?.currency ?? ""}`;

  async function enable() {
    setErr(null);
    try {
      await api("/system/trading/enable", { method: "POST", body: JSON.stringify({ password: pw, totp }) });
      setOpen(false);
      setPw("");
      setTotp("");
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function disable() {
    await api("/system/trading/disable", { method: "POST" });
    onChange();
  }

  return (
    <Card
      title="Moteur de risque"
      right={risk && (risk.entry_allowed ? <Badge tone="ok">✓ Entrées autorisées</Badge> : <Badge tone="bad">⏸ Entrées bloquées</Badge>)}
    >
      <div className="flex items-center justify-between gap-3 rounded-lg bg-panel-2 px-3 py-2.5">
        <div className="text-sm">
          Trading <b className={tradingEnabled ? "text-up" : "text-muted"}>{tradingEnabled ? "activé" : "désactivé"}</b>
          <span className="block text-[11px] text-faint">{tradingEnabled ? "le moteur peut approuver des ordres (papier)" : "aucun ordre ne peut être approuvé"}</span>
        </div>
        {tradingEnabled ? (
          isTrader && <button onClick={disable} className="rounded-lg border border-line px-3 py-1.5 text-xs text-muted hover:text-text">Désactiver</button>
        ) : (
          isAdmin && !killSwitch && <button onClick={() => setOpen(true)} className="rounded-lg bg-gold px-3 py-1.5 text-xs font-semibold text-black">Activer…</button>
        )}
      </div>
      {open && (
        <div className="mt-3 space-y-2 rounded-lg border border-gold/30 bg-gold-soft p-3">
          <p className="text-xs text-muted">Activation explicite : mot de passe + code 2FA. Toutes les vérifications de risque restent actives.</p>
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Mot de passe" className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
          <input value={totp} onChange={(e) => setTotp(e.target.value)} placeholder="Code 2FA" inputMode="numeric" className="num w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm" />
          {err && <p className="text-xs text-danger">{err}</p>}
          <div className="flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="px-2 py-1 text-xs text-muted">Annuler</button>
            <button onClick={enable} className="rounded-lg bg-gold px-3 py-1.5 text-xs font-semibold text-black">Activer le trading</button>
          </div>
        </div>
      )}

      {risk && (
        <>
          {risk.blocks.length > 0 && (
            <ul className="mt-3 space-y-1">
              {risk.blocks.map((b) => <li key={b} className="text-sm text-danger">⏸ {b}</li>)}
            </ul>
          )}
          <div className="mt-4 space-y-3">
            <Meter label="Perte du jour" used={Number(risk.daily_loss)} cap={Number(risk.daily_cap)} fmt={money} />
            <Meter label="Perte de la semaine" used={Number(risk.weekly_loss)} cap={Number(risk.weekly_cap)} fmt={money} />
            <Meter label="Drawdown" used={Number(risk.drawdown) * 100} cap={Number(risk.max_drawdown) * 100} fmt={(x) => `${x.toFixed(2)} %`} />
            <Meter label="Trades du jour" used={risk.trades_today} cap={risk.max_trades} fmt={(x) => String(x)} />
            <Meter label="Pertes consécutives" used={risk.consecutive_losses} cap={risk.max_consecutive_losses} fmt={(x) => String(x)} />
          </div>
          <dl className="num mt-4 grid grid-cols-2 gap-2 border-t border-line-soft pt-3 text-xs">
            <div><dt className="font-sans text-faint">Budget restant du jour</dt><dd className="mt-0.5 text-sm">{money(Number(risk.remaining_daily))}</dd></div>
            <div><dt className="font-sans text-faint">Réalisé aujourd&apos;hui</dt><dd className={`mt-0.5 text-sm ${Number(risk.realized_today) < 0 ? "text-down" : ""}`}>{money(Number(risk.realized_today))}</dd></div>
            {cfg && <div><dt className="font-sans text-faint">Risque par trade</dt><dd className="mt-0.5 text-sm">{(Number(cfg.config.risk_per_trade_pct) * 100).toFixed(2)} %{Number(risk.regime_multiplier) < 1 && <span className="text-warn"> × {risk.regime_multiplier}</span>}</dd></div>}
            <div><dt className="font-sans text-faint">Positions</dt><dd className="mt-0.5 text-sm">{risk.open_positions} / {risk.max_open_positions}</dd></div>
          </dl>
        </>
      )}
      <p className="mt-3 text-[11px] text-faint">🔒 Limites modifiables uniquement par un admin (mot de passe + 2FA). Jamais par l&apos;IA. Disjoncteur automatique au drawdown maximal.</p>
    </Card>
  );
}
