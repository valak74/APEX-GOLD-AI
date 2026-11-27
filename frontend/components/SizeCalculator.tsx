"use client";

import { useState } from "react";
import { api, fmtMoney, type SizingResult } from "@/lib/api";

const REJECTIONS: Record<string, string> = {
  invalid_risk_pct: "Pourcentage de risque invalide (max absolu 2 %)",
  invalid_stop_side: "Stop-loss du mauvais côté du prix d'entrée",
  stop_too_close: "Stop-loss trop proche (niveau minimum du broker)",
  fx_unavailable: "Taux de change indisponible — renseignez un taux USD→devise",
  fx_stale: "Taux de change périmé",
  non_positive_equity: "Équité nulle ou négative",
  below_min_volume: "Position minimale trop risquée pour ce stop et ce capital",
  insufficient_margin: "Marge insuffisante",
};

export function SizeCalculator({ accountCcy }: { accountCcy: string }) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [entry, setEntry] = useState("");
  const [stop, setStop] = useState("");
  const [rate, setRate] = useState("");
  const [res, setRes] = useState<SizingResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function calc(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const body: Record<string, string> = { side, entry, stop_loss: stop };
      if (rate) body.quote_to_account_rate = rate;
      setRes(await api<SizingResult>("/risk/position-size", { method: "POST", body: JSON.stringify(body) }));
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const input = "w-full rounded border border-line bg-panel-2 px-2 py-1.5 num";
  return (
    <div className="space-y-3">
      <form onSubmit={calc} className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-5">
        <select value={side} onChange={(e) => setSide(e.target.value as "buy" | "sell")} className={input}>
          <option value="buy">Achat</option>
          <option value="sell">Vente</option>
        </select>
        <input className={input} placeholder="Entrée" value={entry} onChange={(e) => setEntry(e.target.value)} inputMode="decimal" />
        <input className={input} placeholder="Stop-loss" value={stop} onChange={(e) => setStop(e.target.value)} inputMode="decimal" />
        {accountCcy !== "USD" ? (
          <input className={input} placeholder={`1 USD = ? ${accountCcy}`} value={rate} onChange={(e) => setRate(e.target.value)} inputMode="decimal" />
        ) : <span />}
        <button className="rounded bg-gold px-3 py-1.5 font-medium text-black">Calculer</button>
      </form>
      {err && <p className="text-sm text-down">{err}</p>}
      {res && (
        <div className="text-sm">
          {res.approved ? (
            <p>
              Taille : <b className="num text-gold">{res.volume} lot</b> · risque réel {fmtMoney(res.risk_amount)} (
              {(Number(res.risk_pct_actual) * 100).toFixed(3)} %) · marge {fmtMoney(res.margin_required)}
            </p>
          ) : (
            <p className="text-down">Refusé : {res.rejections.map((r) => REJECTIONS[r] ?? r).join(" · ")}</p>
          )}
          {res.loss_per_lot && <p className="text-muted">Perte par lot au stop (frais inclus) : {fmtMoney(res.loss_per_lot)} · budget {fmtMoney(res.risk_budget)}</p>}
          {res.warnings.map((w) => <p key={w} className="text-paper">{w}</p>)}
        </div>
      )}
      <p className="text-xs text-muted">Outil de simulation : ne passe aucun ordre.</p>
    </div>
  );
}
