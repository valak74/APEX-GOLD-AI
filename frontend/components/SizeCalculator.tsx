"use client";

import { useState } from "react";
import { api, fmtMoney, type SizingResult } from "@/lib/api";
import { Card, Segmented } from "./ui";

const REJECTIONS: Record<string, string> = {
  invalid_risk_pct: "Pourcentage de risque invalide (maximum absolu 2 %)",
  invalid_stop_side: "Le stop-loss est du mauvais côté du prix d'entrée",
  stop_too_close: "Stop-loss trop proche (minimum imposé par le broker)",
  fx_unavailable: "Taux de change indisponible — renseignez le taux USD → devise",
  fx_stale: "Taux de change périmé",
  non_positive_equity: "Équité nulle ou négative",
  below_min_volume: "Même la position minimale dépasserait votre risque autorisé",
  insufficient_margin: "Marge insuffisante",
};

export function SizeCalculator({ accountCcy, price }: { accountCcy: string; price: number | null }) {
  const [side, setSide] = useState<"Achat" | "Vente">("Achat");
  const [entry, setEntry] = useState("");
  const [stop, setStop] = useState("");
  const [rate, setRate] = useState("");
  const [res, setRes] = useState<SizingResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function calc(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const body: Record<string, string> = { side: side === "Achat" ? "buy" : "sell", entry: entry || String(price ?? ""), stop_loss: stop };
      if (rate) body.quote_to_account_rate = rate;
      setRes(await api<SizingResult>("/risk/position-size", { method: "POST", body: JSON.stringify(body) }));
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const input = "num w-full rounded-lg border border-line bg-bg/60 px-3 py-2 text-sm outline-none transition focus:border-gold";
  return (
    <Card title="Taille de position" right={<span className="text-[11px] text-faint">simulation · aucun ordre</span>}>
      <form onSubmit={calc} className="space-y-3">
        <Segmented label="Sens" value={side} options={["Achat", "Vente"] as const} onChange={setSide} />
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-muted">
            Entrée
            <input className={`${input} mt-1`} placeholder={price ? price.toFixed(2) : "4000.00"} value={entry} onChange={(e) => setEntry(e.target.value)} inputMode="decimal" />
          </label>
          <label className="text-xs text-muted">
            Stop-loss
            <input className={`${input} mt-1`} placeholder="ex. 3995.00" value={stop} onChange={(e) => setStop(e.target.value)} inputMode="decimal" required />
          </label>
        </div>
        {res?.rejections.includes("fx_unavailable") && accountCcy !== "USD" && (
          <label className="block text-xs text-muted">
            1 USD = ? {accountCcy}
            <input className={`${input} mt-1`} value={rate} onChange={(e) => setRate(e.target.value)} inputMode="decimal" />
          </label>
        )}
        <button className="w-full rounded-lg bg-gold py-2 text-sm font-semibold text-black transition hover:brightness-110">Calculer</button>
      </form>
      {err && <p className="mt-3 text-sm text-danger">{err}</p>}
      {res && (
        <div className="mt-4 rounded-xl border border-line-soft bg-bg/50 p-4" aria-live="polite">
          {res.approved ? (
            <>
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-muted">Volume</span>
                <span className="num text-2xl font-semibold text-gold">{res.volume} lot</span>
              </div>
              <dl className="num mt-3 space-y-1.5 text-sm">
                <Row k="Risque réel" v={`${fmtMoney(res.risk_amount)} · ${(Number(res.risk_pct_actual) * 100).toFixed(3)} %`} />
                <Row k="Budget" v={fmtMoney(res.risk_budget)} />
                <Row k="Marge requise" v={fmtMoney(res.margin_required)} />
                <Row k="Perte / lot au stop" v={fmtMoney(res.loss_per_lot)} />
              </dl>
            </>
          ) : (
            <p className="text-sm text-danger">✕ {res.rejections.map((r) => REJECTIONS[r] ?? r).join(" · ")}</p>
          )}
          {res.warnings.map((w) => <p key={w} className="mt-2 text-xs text-warn">{w}</p>)}
        </div>
      )}
    </Card>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="font-sans text-muted">{k}</dt>
      <dd>{v}</dd>
    </div>
  );
}
