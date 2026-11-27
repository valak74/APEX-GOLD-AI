"use client";

import { useState } from "react";
import { api, fmtMoney, fmtNum, fmtTime, STATUS_FR, type Narrative, type Opportunity, type RiskDecision } from "@/lib/api";
import { Badge, Card } from "./ui";

function Dir({ d }: { d: Opportunity["direction"] }) {
  return d === "bullish" ? <span className="text-up">▲ Achat</span> : <span className="text-down">▼ Vente</span>;
}

function Quality({ q }: { q: number }) {
  return (
    <span className="inline-flex items-center gap-1.5" title="Part des confluences satisfaites — heuristique, pas une probabilité">
      <span className="h-1.5 w-10 overflow-hidden rounded-full bg-elev">
        <span className="block h-full rounded-full bg-gold" style={{ width: `${q}%` }} />
      </span>
      <span className="num text-xs text-muted">{q}</span>
    </span>
  );
}

export function OpportunitiesList({ items, selected, onSelect }: {
  items: Opportunity[];
  selected: string | null;
  onSelect: (o: Opportunity | null) => void;
}) {
  const active = items.filter((o) => o.status === "active");
  return (
    <Card title={`Opportunités détectées · ${active.length} active${active.length > 1 ? "s" : ""}`} pad={false}>
      {items.length === 0 ? (
        <p className="px-5 pb-5 pt-2 text-sm text-faint">Aucune opportunité pour l&apos;instant. Le scanner analyse le marché toutes les 30 secondes.</p>
      ) : (
        <ul className="max-h-[340px] overflow-y-auto px-2 pb-2">
          {items.slice(0, 12).map((o) => {
            const sel = o.id === selected;
            return (
              <li key={o.id}>
                <button
                  onClick={() => onSelect(sel ? null : o)}
                  aria-pressed={sel}
                  className={`w-full rounded-xl px-3 py-2.5 text-left transition ${sel ? "bg-gold-soft ring-1 ring-gold/40" : "hover:bg-elev/60"} ${o.status !== "active" ? "opacity-55" : ""}`}
                >
                  <div className="flex items-center justify-between gap-2 text-sm">
                    <span className="font-medium"><Dir d={o.direction} /> <span className="num text-xs text-muted">{o.timeframe}</span></span>
                    <Quality q={o.quality} />
                  </div>
                  <div className="mt-0.5 truncate text-xs text-muted">{o.setup_label}</div>
                  <div className="mt-1 flex items-center gap-2 text-[11px]">
                    <span className="num text-faint">{fmtTime(o.detected_at)}</span>
                    <span className="num text-faint">R:R {o.risk_reward.toFixed(2)}</span>
                    {o.blockers.length > 0 && o.status === "active" ? <span className="text-danger">⏸ bloquée</span> : <span className="text-faint">{STATUS_FR[o.status]}</span>}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

export function OpportunityDetail({ o, canExplain, canEvaluate, onClose, onExplained }: {
  o: Opportunity;
  canExplain: boolean;
  canEvaluate: boolean;
  onClose: () => void;
  onExplained: (n: Narrative) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [decision, setDecision] = useState<RiskDecision | null>(null);
  const [checking, setChecking] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const risk = Math.abs(o.entry - o.stop);

  async function evaluateRisk() {
    setChecking(true);
    setErr(null);
    try {
      setDecision(await api<RiskDecision>(`/risk/evaluate/${o.id}`, { method: "POST" }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setChecking(false);
    }
  }
  const s = o.stats;

  async function explain() {
    setBusy(true);
    setErr(null);
    try {
      onExplained(await api<Narrative>(`/opportunities/${o.id}/explain`, { method: "POST" }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Pourquoi cette opportunité ?"
      right={<button onClick={onClose} className="text-xs text-muted hover:text-text">Fermer ✕</button>}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-lg font-semibold"><Dir d={o.direction} /></span>
        <span className="text-sm text-muted">{o.setup_label} · {o.timeframe} · {fmtTime(o.detected_at, true)}</span>
        <Badge tone={o.status === "active" ? (o.blockers.length ? "bad" : "gold") : "neutral"}>
          {o.status === "active" && o.blockers.length ? "Trading interdit maintenant" : STATUS_FR[o.status]}
        </Badge>
      </div>

      {o.blockers.length > 0 && (
        <div className="mt-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          ⏸ {o.blockers.join(" · ")}
        </div>
      )}

      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        <div className="space-y-4">
          <div>
            <h3 className="label mb-2">Plan</h3>
            <dl className="num grid grid-cols-3 gap-2 text-sm">
              <Plan k="Entrée" v={fmtNum(o.entry)} tone="text-gold" />
              <Plan k="Stop" v={fmtNum(o.stop)} sub={`−${risk.toFixed(2)} $`} tone="text-danger" />
              <Plan k={`Objectif${o.targets.length > 1 ? "s" : ""}`} v={o.targets.map((t) => fmtNum(t)).join(" / ")} sub={`R:R ${o.risk_reward.toFixed(2)}`} tone="text-up" />
            </dl>
            <p className="mt-2 text-xs text-muted">Invalidation : {o.invalidation}</p>
          </div>

          <div>
            <h3 className="label mb-2">Critères vérifiés · qualité {o.quality}/100</h3>
            <ul className="space-y-1.5">
              {o.confluences.map((c) => (
                <li key={c.key} className="flex gap-2.5 text-sm">
                  <span aria-label={c.ok ? "satisfait" : "non satisfait"} className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded text-[10px] font-bold ${c.ok ? "bg-up/20 text-up" : "bg-elev text-faint"}`}>
                    {c.ok ? "✓" : "✕"}
                  </span>
                  <span>
                    <span className={c.ok ? "text-text" : "text-muted"}>{c.label}</span>
                    <span className="block text-xs text-faint">{c.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {o.warnings.length > 0 && (
            <div>
              <h3 className="label mb-1.5">Points d&apos;attention</h3>
              {o.warnings.map((w) => <p key={w} className="text-sm text-warn">⚠ {w}</p>)}
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-line-soft bg-bg/50 p-4">
            <h3 className="label mb-2">Historique de ce motif</h3>
            {s && s.n > 0 && s.hit_rate !== null ? (
              <>
                <p className="text-sm">
                  Sur <b className="num">{s.n}</b> {s.label} passés, le prix a atteint un objectif équivalent avant le stop dans{" "}
                  <b className="num">{(s.hit_rate * 100).toFixed(0)} %</b> des cas
                  {s.hit_rate_ci && <span className="text-muted"> (IC 95 % : {(s.hit_rate_ci[0] * 100).toFixed(0)}–{(s.hit_rate_ci[1] * 100).toFixed(0)} %)</span>}.
                </p>
                {s.breakeven_rate !== null && (
                  <p className="mt-1 text-xs text-muted">Seuil de rentabilité à ce R:R (hors frais) : {(s.breakeven_rate * 100).toFixed(0)} %.</p>
                )}
                {s.small_sample && <p className="mt-2 text-xs text-warn">Échantillon trop petit : aucun avantage n&apos;est prouvé.</p>}
              </>
            ) : (
              <p className="text-sm text-faint">Pas assez d&apos;historique sur cette unité de temps pour mesurer ce motif.</p>
            )}
          </div>

          <div className="rounded-xl border border-gold/25 bg-gold-soft p-4">
            <h3 className="label mb-2 !text-gold">Analyse rédigée par l&apos;IA</h3>
            {o.narrative ? (
              <div className="space-y-2 text-sm">
                <p>{o.narrative.resume}</p>
                <ul className="list-disc space-y-1 pl-5 text-muted">{o.narrative.pourquoi.map((x) => <li key={x}>{x}</li>)}</ul>
                <p className="pt-1 text-xs font-medium uppercase tracking-wider text-faint">Risques</p>
                <ul className="list-disc space-y-1 pl-5 text-muted">{o.narrative.risques.map((x) => <li key={x}>{x}</li>)}</ul>
                <p className="text-xs text-muted">Invalidation : {o.narrative.invalidation}</p>
                <p className="text-xs italic text-faint">{o.narrative.prudence} — rédigé par {o.narrative.model} à partir des seuls faits ci-contre.</p>
              </div>
            ) : canExplain ? (
              <>
                <button onClick={explain} disabled={busy} className="rounded-lg bg-gold px-3 py-1.5 text-sm font-semibold text-black disabled:opacity-50">
                  {busy ? "Rédaction…" : "Demander l'analyse de l'IA"}
                </button>
                {err && <p className="mt-2 text-xs text-danger">{err}</p>}
              </>
            ) : (
              <p className="text-sm text-faint">Rédaction IA non disponible (rôle trader requis ou IA désactivée).</p>
            )}
          </div>
        </div>
      </div>
      <div className="mt-5 rounded-xl border border-line bg-panel-2 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="label">Vérification par le moteur de risque</h3>
          {canEvaluate && o.status === "active" && (
            <button onClick={evaluateRisk} disabled={checking} className="rounded-lg border border-gold/40 bg-gold-soft px-3 py-1.5 text-xs font-semibold text-gold disabled:opacity-50">
              {checking ? "Vérification…" : decision ? "Revérifier maintenant" : "Vérifier maintenant"}
            </button>
          )}
        </div>
        {!decision && (
          <p className="mt-2 text-sm text-faint">
            {o.status !== "active" ? "Cette opportunité n'est plus active." : "Le moteur vérifie 23 contrôles (limites, coûts, marché, données) et calcule la taille. Aucun ordre n'est passé."}
          </p>
        )}
        {decision && (
          <div className="mt-3">
            {decision.approved ? (
              <div className="rounded-lg border border-up/30 bg-up/10 px-3 py-2 text-sm">
                <b className="text-up">✓ Autorisée</b> · <span className="num">{decision.order?.volume} lot</span> · risque{" "}
                <span className="num">{fmtMoney(decision.sizing?.risk_amount)}</span> ({(Number(decision.risk_pct_effective) * 100).toFixed(3)} %) · R:R net{" "}
                <span className="num">{decision.net_risk_reward}</span>
              </div>
            ) : (
              <div className={`rounded-lg border px-3 py-2 text-sm ${decision.critical ? "border-danger/50 bg-danger/15" : "border-danger/30 bg-danger/10"} text-danger`}>
                <b>✕ Refusée</b> — {decision.checks.filter((c) => !c.passed).map((c) => c.label + (c.value ? ` (${c.value}${c.limit ? ` / ${c.limit}` : ""})` : "")).join(" · ")}
              </div>
            )}
            {decision.reductions.length > 0 && <p className="mt-2 text-xs text-warn">Risque réduit : {decision.reductions.join(" · ")}</p>}
            <button onClick={() => setShowAll((v) => !v)} className="mt-2 text-xs text-muted hover:text-text">
              {showAll ? "Masquer" : "Voir"} les {decision.checks.length} contrôles
            </button>
            {showAll && (
              <ul className="mt-2 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                {decision.checks.map((c) => (
                  <li key={c.code} className="flex justify-between gap-2 border-b border-line-soft py-1">
                    <span className={c.passed ? "text-muted" : "text-danger"}>{c.passed ? "✓" : "✕"} {c.label}</span>
                    <span className="num shrink-0 text-faint">{c.value}{c.limit ? ` / ${c.limit}` : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
      <p className="mt-4 text-[11px] text-faint">Une opportunité n&apos;est pas un ordre : seul le moteur de risque peut l&apos;autoriser, et l&apos;exécution automatique viendra avec la phase de paper trading.</p>
    </Card>
  );
}

function Plan({ k, v, sub, tone }: { k: string; v: string; sub?: string; tone: string }) {
  return (
    <div className="rounded-lg bg-panel-2 px-3 py-2">
      <dt className="font-sans text-xs text-muted">{k}</dt>
      <dd className={`mt-0.5 ${tone}`}>{v}</dd>
      {sub && <dd className="text-[11px] text-faint">{sub}</dd>}
    </div>
  );
}
