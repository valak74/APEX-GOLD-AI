"use client";

import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError, SETUP_FR, toDate, type BacktestRequest, type Holdout, type Setup } from "@/lib/api";
import { Card, Segmented } from "../ui";

const SESSIONS = [
  { key: "asia", label: "Asie" },
  { key: "london", label: "Londres" },
  { key: "newyork", label: "New York" },
] as const;

const DEFAULTS: BacktestRequest = {
  setup: "liquidity_sweep_reversal",
  timeframe: "5m",
  start: null,
  end: null,
  stop_buffer_atr: 0.2,
  min_quality: 0,
  require_htf_alignment: false,
  max_hold_bars: 48,
  sessions: ["asia", "london", "newyork"],
  spread_mult: 1,
  slippage: 0.05,
  swap_per_lot_night: 0,
  initial_balance: 10000,
};

const input = "num w-full rounded-lg border border-line bg-bg/60 px-3 py-2 text-sm outline-none transition focus:border-gold";

export function BacktestForm({ canRun, isAdmin, onStarted }: { canRun: boolean; isAdmin: boolean; onStarted: (id: number) => void }) {
  const [f, setF] = useState<BacktestRequest>(DEFAULTS);
  const [hold, setHold] = useState<Holdout | null>(null);
  useEffect(() => {
    api<Holdout>("/research/holdout").then(setHold).catch(() => setHold(null));
  }, []);
  const holdDay = hold?.start ? toDate(hold.start).toLocaleDateString("fr-FR", { day: "2-digit", month: "long", year: "numeric" }) : null;
  const [adv, setAdv] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = <K extends keyof BacktestRequest>(k: K, v: BacktestRequest[K]) => setF((x) => ({ ...x, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const body = { ...f, start: f.start ? `${f.start}T00:00:00Z` : null, end: f.end ? `${f.end}T00:00:00Z` : null };
      const r = await api<{ id: number }>("/backtests", { method: "POST", body: JSON.stringify(body) });
      onStarted(r.id);
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Échec du lancement");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Nouveau backtest" right={<span className="text-[11px] text-faint">historique stocké · aucun ordre</span>}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Stratégie">
          <div className="grid grid-cols-2 gap-1.5">
            {(Object.keys(SETUP_FR) as Setup[]).map((s) => (
              <button
                type="button"
                key={s}
                onClick={() => set("setup", s)}
                aria-pressed={f.setup === s}
                className={`rounded-lg border px-3 py-2 text-left text-xs transition ${f.setup === s ? "border-gold/60 bg-gold-soft text-text" : "border-line text-muted hover:text-text"}`}
              >
                {SETUP_FR[s]}
              </button>
            ))}
          </div>
        </Field>
        <div className="flex flex-wrap items-end gap-4">
          <Field label="Unité de temps">
            <Segmented label="Unité de temps" value={f.timeframe} options={["5m", "15m", "1h"] as const} onChange={(v) => set("timeframe", v)} />
          </Field>
          <Field label="Capital (USD)" className="flex-1">
            <input className={input} type="number" min={100} step={100} value={f.initial_balance} onChange={(e) => set("initial_balance", Number(e.target.value))} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Du">
            <input className={input} type="date" value={f.start ?? ""} onChange={(e) => set("start", e.target.value || null)} />
          </Field>
          <Field label="Au">
            <input className={input} type="date" value={f.end ?? ""} onChange={(e) => set("end", e.target.value || null)} />
          </Field>
        </div>
        <Field label="Séances">
          <div className="flex gap-1.5">
            {SESSIONS.map((s) => {
              const on = f.sessions.includes(s.key);
              return (
                <button
                  type="button"
                  key={s.key}
                  aria-pressed={on}
                  onClick={() => set("sessions", on ? f.sessions.filter((x) => x !== s.key) : [...f.sessions, s.key])}
                  className={`flex-1 rounded-lg border px-2 py-1.5 text-xs transition ${on ? "border-gold/60 bg-gold-soft text-text" : "border-line text-faint hover:text-muted"}`}
                >
                  {s.label}
                </button>
              );
            })}
          </div>
        </Field>

        <button type="button" onClick={() => setAdv(!adv)} className="text-xs text-muted hover:text-text" aria-expanded={adv}>
          {adv ? "▾" : "▸"} Paramètres avancés et coûts
        </button>
        {adv && (
          <div className="grid grid-cols-2 gap-3 rounded-xl border border-line-soft bg-bg/40 p-3">
            <Field label="Marge du stop (× ATR)">
              <input className={input} type="number" min={0} max={2} step={0.05} value={f.stop_buffer_atr} onChange={(e) => set("stop_buffer_atr", Number(e.target.value))} />
            </Field>
            <Field label="Qualité minimale">
              <input className={input} type="number" min={0} max={100} step={5} value={f.min_quality} onChange={(e) => set("min_quality", Number(e.target.value))} />
            </Field>
            <Field label="Durée max (bougies)">
              <input className={input} type="number" min={1} max={2000} value={f.max_hold_bars} onChange={(e) => set("max_hold_bars", Number(e.target.value))} />
            </Field>
            <Field label="Spread (× enregistré)">
              <input className={input} type="number" min={0.5} max={5} step={0.1} value={f.spread_mult} onChange={(e) => set("spread_mult", Number(e.target.value))} />
            </Field>
            <Field label="Glissement ($/once)">
              <input className={input} type="number" min={0} max={5} step={0.01} value={f.slippage} onChange={(e) => set("slippage", Number(e.target.value))} />
            </Field>
            <Field label="Swap (USD/lot/nuit)">
              <input className={input} type="number" min={-100} max={100} step={0.5} value={f.swap_per_lot_night} onChange={(e) => set("swap_per_lot_night", Number(e.target.value))} />
            </Field>
            <label className="col-span-2 flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={f.require_htf_alignment} onChange={(e) => set("require_htf_alignment", e.target.checked)} className="accent-[#e0b55b]" />
              Exiger l&apos;alignement avec la tendance de fond (4 h)
            </label>
          </div>
        )}

        {holdDay && (
          <div className="rounded-xl border border-line-soft bg-bg/40 p-3 text-xs text-muted">
            {f.final_holdout ? (
              <span className="text-warn">
                Verdict final : le test portera uniquement sur la période réservée depuis le {holdDay}. Déjà consultée {hold?.consultations ?? 0} fois — chaque consultation affaiblit sa valeur de preuve.
              </span>
            ) : (
              <>Recherche jusqu&apos;au {holdDay}. La suite est réservée au verdict final et n&apos;est jamais utilisée ici.</>
            )}
            {isAdmin && (
              <label className="mt-2 flex items-center gap-2 text-muted">
                <input type="checkbox" checked={!!f.final_holdout} onChange={(e) => set("final_holdout", e.target.checked)} className="accent-[#e0b55b]" />
                Verdict final sur la période réservée
              </label>
            )}
          </div>
        )}
        <button
          disabled={!canRun || busy || f.sessions.length === 0}
          className="w-full rounded-lg bg-gold py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Lancement…" : "Lancer le backtest"}
        </button>
        {!canRun && <p className="text-xs text-faint">Rôle « chercheur » ou plus requis pour lancer un backtest.</p>}
        {err && <p className="text-sm text-danger">{err}</p>}
      </form>
    </Card>
  );
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <div className="mb-1 text-xs text-muted">{label}</div>
      {children}
    </div>
  );
}
