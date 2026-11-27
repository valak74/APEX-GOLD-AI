"use client";

import { useEffect, useState } from "react";
import { api, ApiError, SETUP_FR, toDate, type AutopilotConfig, type PaperStatus, type Setup } from "@/lib/api";
import { Badge, Card, Dot } from "../ui";

const input = "num w-full rounded-lg border border-line bg-bg/60 px-3 py-2 text-sm outline-none transition focus:border-gold";

export function AutopilotCard({ s, canUse, onChange }: { s: PaperStatus; canUse: boolean; onChange: (s: PaperStatus) => void }) {
  const [cfg, setCfg] = useState<AutopilotConfig>(s.config);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setCfg(s.config), [s.config]);
  const dirty = JSON.stringify(cfg) !== JSON.stringify(s.config);

  async function send(enabled: boolean) {
    setBusy(true);
    setErr(null);
    try {
      onChange(await api<PaperStatus>("/paper/autopilot", { method: "POST", body: JSON.stringify({ enabled, config: cfg }) }));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Échec");
    } finally {
      setBusy(false);
    }
  }
  const toggle = <T,>(arr: T[], v: T) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const checks = [
    { ok: s.available, label: "Mode PAPER (argent simulé)" },
    { ok: !s.kill_switch, label: "Arrêt d'urgence non engagé" },
    { ok: s.trading_enabled, label: "Trading activé (admin, mot de passe + code)" },
  ];
  const ready = checks.every((c) => c.ok);
  const alive = s.last_cycle && Date.now() - toDate(s.last_cycle).getTime() < 30_000;

  return (
    <Card
      title="Pilote automatique"
      right={s.enabled ? <Badge tone="ok"><Dot tone="ok" /> en marche</Badge> : <Badge>à l&apos;arrêt</Badge>}
    >
      <ul className="space-y-1.5">
        {checks.map((c) => (
          <li key={c.label} className="flex items-center gap-2 text-xs">
            <span aria-hidden className={c.ok ? "text-ok" : "text-danger"}>{c.ok ? "✓" : "✕"}</span>
            <span className={c.ok ? "text-muted" : "text-text"}>{c.label}</span>
          </li>
        ))}
      </ul>

      <div className="mt-4 space-y-3">
        <div>
          <div className="mb-1 text-xs text-muted">Stratégies</div>
          <div className="grid grid-cols-2 gap-1.5">
            {(Object.keys(SETUP_FR) as Setup[]).map((k) => {
              const on = cfg.setups.includes(k);
              return (
                <button key={k} type="button" aria-pressed={on} disabled={!canUse} onClick={() => setCfg({ ...cfg, setups: toggle(cfg.setups, k) })}
                  className={`rounded-lg border px-3 py-2 text-left text-xs transition ${on ? "border-gold/60 bg-gold-soft text-text" : "border-line text-faint hover:text-muted"}`}>
                  {SETUP_FR[k]}
                </button>
              );
            })}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div>
            <div className="mb-1 text-xs text-muted">Unités</div>
            <div className="flex gap-1">
              {(["5m", "15m"] as const).map((t) => {
                const on = cfg.timeframes.includes(t);
                return (
                  <button key={t} type="button" aria-pressed={on} disabled={!canUse} onClick={() => setCfg({ ...cfg, timeframes: toggle(cfg.timeframes, t) })}
                    className={`num flex-1 rounded-lg border px-1 py-2 text-xs transition ${on ? "border-gold/60 bg-gold-soft text-text" : "border-line text-faint"}`}>
                    {t}
                  </button>
                );
              })}
            </div>
          </div>
          <label className="text-xs text-muted">
            Qualité min.
            <input className={`${input} mt-1`} type="number" min={0} max={100} step={5} disabled={!canUse} value={cfg.min_quality} onChange={(e) => setCfg({ ...cfg, min_quality: Number(e.target.value) })} />
          </label>
          <label className="text-xs text-muted">
            Durée max
            <input className={`${input} mt-1`} type="number" min={1} max={500} disabled={!canUse} value={cfg.max_hold_bars} onChange={(e) => setCfg({ ...cfg, max_hold_bars: Number(e.target.value) })} />
          </label>
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        {s.enabled ? (
          <>
            <button onClick={() => send(true)} disabled={!canUse || busy || !dirty} className="flex-1 rounded-lg border border-line py-2.5 text-sm font-medium text-text transition hover:bg-elev disabled:opacity-40">
              Appliquer
            </button>
            <button onClick={() => send(false)} disabled={!canUse || busy} className="flex-1 rounded-lg border border-danger/50 bg-danger/10 py-2.5 text-sm font-semibold text-danger transition hover:bg-danger/20 disabled:opacity-40">
              Arrêter
            </button>
          </>
        ) : (
          <button onClick={() => send(true)} disabled={!canUse || busy || !ready || cfg.setups.length === 0 || cfg.timeframes.length === 0}
            className="w-full rounded-lg bg-gold py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40">
            Démarrer en PAPER
          </button>
        )}
      </div>
      {err && <p className="mt-2 text-sm text-danger">{err}</p>}
      {!canUse && <p className="mt-2 text-xs text-faint">Rôle « trader » ou plus requis.</p>}
      <p className="num mt-3 text-[10.5px] text-faint">
        {s.last_cycle ? `Dernier passage ${toDate(s.last_cycle).toLocaleTimeString("fr-FR")}${alive ? "" : " — boucle inactive"}` : "Boucle non démarrée"}
        {s.last_error && ` · erreur : ${s.last_error}`}
      </p>
      <p className="mt-2 text-[10.5px] leading-relaxed text-faint">
        Chaque idée fraîche des stratégies choisies passe par les contrôles de risque au prix du moment, une seule fois. Stop et objectif posés à l&apos;ouverture, sortie au bout de la durée maximale et avant la fermeture du vendredi.
      </p>
    </Card>
  );
}
