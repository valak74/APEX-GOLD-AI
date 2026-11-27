"use client";

import { fmtMoney, fmtNum, type Account, type Position, type Quality } from "@/lib/api";
import { Badge, Card, Delta } from "./ui";

export function AccountCard({ account }: { account: Account }) {
  const upnl = Number(account.equity.amount) - Number(account.balance.amount);
  const used = Number(account.margin_used.amount);
  const eq = Number(account.equity.amount);
  const usePct = eq > 0 ? Math.min(100, (used / eq) * 100) : 0;
  return (
    <Card title="Compte" right={<span className="num text-xs text-faint">{account.account_id}</span>}>
      <div className="label !text-[10px]">Équité</div>
      <div className="num mt-1 text-[34px] font-semibold leading-none tracking-tight">
        {fmtNum(account.equity.amount)} <span className="text-lg font-normal text-muted">{account.currency}</span>
      </div>
      <div className="mt-2 text-sm">
        <span className="text-muted">P&amp;L latent </span>
        <Delta value={upnl} suffix={` ${account.currency}`} />
      </div>
      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <Kv k="Solde" v={fmtMoney(account.balance)} />
        <Kv k="Marge libre" v={fmtMoney(account.free_margin)} />
        <Kv k="Marge utilisée" v={fmtMoney(account.margin_used)} />
        <Kv k="Levier" v={`1:${account.leverage}`} />
      </dl>
      <div className="mt-4">
        <div className="mb-1.5 flex justify-between text-xs text-muted">
          <span>Utilisation de la marge</span>
          <span className="num">{usePct.toFixed(1)} %</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-elev" role="meter" aria-valuenow={usePct} aria-valuemin={0} aria-valuemax={100} aria-label="Utilisation de la marge">
          <div className={`h-full rounded-full ${usePct > 50 ? "bg-danger" : usePct > 25 ? "bg-warn" : "bg-gold"}`} style={{ width: `${usePct}%` }} />
        </div>
      </div>
    </Card>
  );
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{k}</dt>
      <dd className="num mt-0.5">{v}</dd>
    </div>
  );
}

const ISSUES: Record<string, string> = {
  no_data: "Aucune donnée reçue",
  stale: "Prix figé (plus de 10 s)",
  market_closed: "Marché fermé",
  warming_up: "Calibrage en cours (30 cotations minimum)",
  recent_spread_spike: "Pic de spread récent",
  recent_price_jump: "Saut de prix anormal récent",
  recent_out_of_order: "Horodatages désordonnés",
  recent_future_timestamp: "Horodatage dans le futur",
};

export function QualityCard({ q }: { q: Quality | null }) {
  const score = q ? Math.round(q.score * 100) : 0;
  const tone = !q ? "off" : q.tradeable ? "ok" : score >= 50 ? "warn" : "bad";
  const color = { ok: "#22c08a", warn: "#f2a600", bad: "#eb5757", off: "#5b6375" }[tone];
  const R = 34, C = 2 * Math.PI * R;
  return (
    <Card
      title="Qualité des données"
      right={q ? <Badge tone={q.tradeable ? "ok" : "warn"}>{q.tradeable ? "✓ Tradable" : "✕ Trading bloqué"}</Badge> : null}
    >
      <div className="flex items-center gap-5">
        <svg width="84" height="84" viewBox="0 0 84 84" role="img" aria-label={`Score de qualité ${score} sur 100`}>
          <circle cx="42" cy="42" r={R} fill="none" stroke="#1c212c" strokeWidth="7" />
          <circle cx="42" cy="42" r={R} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round"
            strokeDasharray={`${(score / 100) * C} ${C}`} transform="rotate(-90 42 42)" style={{ transition: "stroke-dasharray .6s" }} />
          <text x="42" y="47" textAnchor="middle" className="num" fontSize="20" fontWeight="600" fill="#eceef3">{q ? score : "—"}</text>
        </svg>
        <div className="min-w-0 flex-1 space-y-1.5 text-sm">
          {q && q.issues.length === 0 && <p className="text-muted">Flux propre, aucun problème détecté.</p>}
          {q?.issues.map((i) => (
            <p key={i} className="flex items-start gap-2 text-muted">
              <span aria-hidden className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />
              {ISSUES[i] ?? i}
            </p>
          ))}
        </div>
      </div>
      {q && (
        <dl className="num mt-4 grid grid-cols-3 gap-2 border-t border-line-soft pt-3 text-xs">
          <div><dt className="text-faint">Spread</dt><dd className="mt-0.5 text-text">{fmtNum(q.spread)}</dd></div>
          <div><dt className="text-faint">Médian</dt><dd className="mt-0.5 text-text">{fmtNum(q.spread_median)}</dd></div>
          <div><dt className="text-faint">Cotations</dt><dd className="mt-0.5 text-text">{(q.counters.quotes ?? 0).toLocaleString("fr-FR")}</dd></div>
        </dl>
      )}
    </Card>
  );
}

export function PositionsCard({ positions, ccy }: { positions: Position[]; ccy: string }) {
  return (
    <Card title={`Positions ouvertes · ${positions.length}`} pad={false}>
      {positions.length === 0 ? (
        <p className="px-5 pb-5 pt-3 text-sm text-faint">Aucune position ouverte.</p>
      ) : (
        <div className="overflow-x-auto px-2 pb-3">
          <table className="num w-full min-w-[560px] text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-faint">
                {["Sens", "Volume", "Entrée", "Stop", "Objectif", "P&L"].map((h) => (
                  <th key={h} className={`px-3 py-2 font-medium ${h === "P&L" ? "text-right" : ""}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.position_id} className="border-t border-line-soft">
                  <td className="px-3 py-2.5">
                    <Badge tone={p.side === "buy" ? "ok" : "warn"}>{p.side === "buy" ? "▲ Achat" : "▼ Vente"}</Badge>
                  </td>
                  <td className="px-3">{p.volume}</td>
                  <td className="px-3">{fmtNum(p.open_price)}</td>
                  <td className="px-3 text-muted">{fmtNum(p.stop_loss)}</td>
                  <td className="px-3 text-muted">{fmtNum(p.take_profit)}</td>
                  <td className="px-3 text-right">
                    <Delta value={p.unrealized_pnl ? Number(p.unrealized_pnl.amount) : null} suffix={` ${ccy}`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
