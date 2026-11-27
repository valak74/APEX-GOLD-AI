"use client";

import { useMemo, useState } from "react";
import { SETUP_FR, toDate, type CompRow, type DiagDetail, type DiagReport, type ExitRow, type Setup } from "@/lib/api";
import { Badge, Card, Segmented } from "../ui";

const KEY_FR: Record<string, string> = { tout: "Ensemble", "séance": "Séance", "régime": "Régime", "volatilité": "Volatilité", "tendance de fond": "Tendance de fond" };
const fr2 = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const day = (s: string) => toDate(s).toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });

/** Cell styling: only "retained" results are highlighted; uncorrected noise stays faint. */
function cellCls(r: { retained?: boolean; significant?: boolean } | undefined, v: number | null | undefined) {
  if (!r || v == null) return "text-faint";
  if (r.retained) return `font-semibold ${v > 0 ? "bg-up/15 text-up ring-1 ring-up/40" : "bg-down/15 text-down ring-1 ring-down/40"}`;
  if (r.significant) return v > 0 ? "text-up/80" : "text-down/80";
  return "text-muted";
}

export function DiagnosticReport({ d }: { d: DiagDetail }) {
  if (d.status === "running") {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <span className="pulse h-3 w-3 rounded-full bg-gold" />
          <p className="text-sm">Diagnostic #{d.id} en cours…</p>
          <p className="max-w-md text-xs text-muted">Détection de tous les éléments sur l&apos;historique de recherche, études d&apos;événements et règles de sortie. Comptez une à quelques minutes.</p>
        </div>
      </Card>
    );
  }
  if (d.status === "error" || !d.report) {
    return (
      <Card title={`Diagnostic #${d.id}`}>
        <p className="text-sm text-danger">✕ {d.error ?? "Rapport indisponible"}</p>
        {d.error?.includes("insuffisant") && (
          <p className="mt-3 text-xs text-muted">
            Importez d&apos;abord l&apos;historique : <code className="num rounded bg-elev px-1.5 py-0.5 text-text">apex import-dukascopy --from 2023-01-01 --to 2026-11-01</code>
          </p>
        )}
      </Card>
    );
  }
  return <Report id={d.id} r={d.report} />;
}

function Report({ id, r }: { id: number; r: DiagReport }) {
  const tfs = r.costs.filter((c) => c.median_atr != null).map((c) => c.timeframe);
  const [tf, setTf] = useState(tfs[0] ?? "5m");
  const [comp, setComp] = useState<string | null>(null);
  const edges =
    r.components.filter((x) => x.retained && x.mean_excess_atr > 0).length +
    r.exits.filter((x) => x.retained && (x.expectancy_r ?? 0) > 0).length;
  const losses = r.retained_total - edges;
  return (
    <div className="space-y-4">
      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-4 p-5">
          <div>
            <div className="label">Diagnostic #{id}</div>
            <h2 className="mt-1 text-lg font-semibold">Qu&apos;est-ce qui prédit vraiment le prix ?</h2>
            <p className="num mt-1 text-xs text-muted">
              {day(r.data.start)} → {day(r.data.end)} · {r.data.bars_5m.toLocaleString("fr-FR")} bougies 5m · {r.tests_total} tests
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <Badge tone={edges > 0 ? "ok" : "neutral"}>{edges > 0 ? `${edges} avantage${edges > 1 ? "s" : ""} retenu${edges > 1 ? "s" : ""}` : "Aucun avantage démontré"}</Badge>
            {losses > 0 && <span className="num text-[11px] text-faint">{losses} effet{losses > 1 ? "s" : ""} contraire{losses > 1 ? "s" : ""} ou perte{losses > 1 ? "s" : ""} démontré{losses > 1 ? "s" : ""}</span>}
          </div>
        </div>
        <ul className="space-y-2.5 border-t border-line-soft p-5">
          {r.findings.map((f) => (
            <li key={f} className="flex gap-3 text-sm leading-relaxed">
              <span aria-hidden className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${f.startsWith("Signal exploitable") || f.startsWith("Meilleure") ? "bg-up" : f.startsWith("À l'inverse") ? "bg-down" : "bg-gold"}`} />
              <span>{f}</span>
            </li>
          ))}
        </ul>
        <p className="border-t border-line-soft px-5 py-3 text-[11px] text-faint">
          Un résultat n&apos;est « retenu » que s&apos;il survit à la correction pour tests multiples, dépasse les coûts, existe sur les deux moitiés de l&apos;historique et repose sur au moins 100 cas. Un résultat retenu reste une hypothèse : il doit encore passer le backtest complet puis le verdict sur la période réservée.
        </p>
      </section>

      <Costs r={r} />

      <Card
        title="Éléments d'analyse : mouvement après détection"
        right={tfs.length > 1 ? <Segmented label="Unité de temps" value={tf} options={tfs} onChange={(v) => { setTf(v); setComp(null); }} /> : undefined}
      >
        <p className="mb-3 text-xs text-muted">
          Mouvement moyen dans le sens attendu, au-delà de la dérive normale, en ATR. Cliquez une ligne pour voir le détail par condition.
        </p>
        <CompTable rows={r.components.filter((x) => x.timeframe === tf && x.condition_key === "tout")} labels={r.labels.components} onPick={setComp} picked={comp} counts={r.event_counts[tf] ?? {}} />
        {comp && <Conditions rows={r.components.filter((x) => x.timeframe === tf && x.component === comp && x.condition_key !== "tout")} title={r.labels.components[comp] ?? comp} />}
        <Legend />
      </Card>

      <Exits rows={r.exits} labels={r.labels.exits} tfs={tfs} />

      <Card title="Méthode">
        <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
          {Object.entries(r.method).map(([k, v]) => (
            <div key={k} className="border-b border-line-soft pb-1.5 text-muted">{v}</div>
          ))}
          <div className="border-b border-line-soft pb-1.5 text-muted">
            Données de recherche jusqu&apos;au {r.data.holdout_start ? day(r.data.holdout_start) : "dernier jour (aucune période réservée)"}
          </div>
          <div className="border-b border-line-soft pb-1.5 text-muted">
            {r.data.previous_diagnostics} diagnostic{r.data.previous_diagnostics > 1 ? "s" : ""} déjà réalisé{r.data.previous_diagnostics > 1 ? "s" : ""} sur ces données
          </div>
        </dl>
      </Card>
    </div>
  );
}

function Costs({ r }: { r: DiagReport }) {
  const rows = r.costs.filter((c) => c.cost_pct_atr != null);
  const max = Math.max(...rows.map((c) => c.cost_pct_atr ?? 0), 1);
  return (
    <Card title="Poids des coûts" right={<span className="text-[11px] text-faint">aller-retour en % d&apos;un ATR</span>}>
      <div className="space-y-3">
        {rows.map((c) => (
          <div key={c.timeframe} className="grid grid-cols-[44px_1fr_auto] items-center gap-3">
            <span className="num text-sm">{c.timeframe}</span>
            <div className="h-2.5 overflow-hidden rounded-full bg-bg/60">
              <div className={`h-full rounded-full ${(c.cost_pct_atr ?? 0) > 12 ? "bg-down" : (c.cost_pct_atr ?? 0) > 7 ? "bg-warn" : "bg-up"}`} style={{ width: `${((c.cost_pct_atr ?? 0) / max) * 100}%` }} />
            </div>
            <span className="num w-36 text-right text-sm">
              {c.cost_pct_atr?.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %
              <span className="ml-1.5 text-[11px] text-faint">ATR {c.median_atr?.toFixed(2)} $</span>
            </span>
          </div>
        ))}
      </div>
      {rows[0] && (
        <p className="mt-4 text-[11px] text-faint">
          Aller-retour : spread {rows[0].median_spread} $ + glissement {rows[0].slippage_rt} $ + commission {rows[0].commission_rt} $ par once = {rows[0].roundtrip_cost} $.
        </p>
      )}
    </Card>
  );
}

function horizonsOf(rows: { horizon: number }[]) {
  return [...new Set(rows.map((x) => x.horizon))].sort((a, b) => a - b);
}

function CompTable({ rows, labels, onPick, picked, counts }: { rows: CompRow[]; labels: Record<string, string>; onPick: (c: string) => void; picked: string | null; counts: Record<string, number> }) {
  const hz = horizonsOf(rows);
  const comps = Object.keys(labels);
  if (!rows.length) return <p className="text-sm text-faint">Pas assez d&apos;événements.</p>;
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="num w-full min-w-[520px] text-left text-xs">
        <thead>
          <tr className="text-[10.5px] uppercase tracking-wider text-faint">
            <th className="pb-2 font-medium">Élément</th>
            <th className="pb-2 font-medium">Cas</th>
            {hz.map((h) => <th key={h} className="pb-2 text-right font-medium">+{h} bougies</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-line-soft">
          {comps.map((c) => {
            const byH = new Map(rows.filter((x) => x.component === c).map((x) => [x.horizon, x]));
            return (
              <tr key={c} onClick={() => onPick(c)} className={`cursor-pointer transition hover:bg-elev/50 ${picked === c ? "bg-elev/70" : ""}`}>
                <td className="py-2 pr-2 font-sans">{labels[c]}</td>
                <td className="text-muted">{counts[c] ?? 0}</td>
                {hz.map((h) => {
                  const x = byH.get(h);
                  return (
                    <td key={h} className="py-1 text-right">
                      <span title={x ? `n = ${x.n} · p = ${x.p.toExponential(1)} · IC [${fr2(x.ci[0])} ; ${fr2(x.ci[1])}] · moitiés ${fr2(x.halves[0])} / ${fr2(x.halves[1])}` : "échantillon trop petit"} className={`inline-block rounded px-1.5 py-0.5 ${cellCls(x, x?.mean_excess_atr)}`}>
                        {x ? fr2(x.mean_excess_atr) : "—"}
                      </span>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Conditions({ rows, title }: { rows: CompRow[]; title: string }) {
  const hz = horizonsOf(rows);
  const groups = [...new Set(rows.map((x) => `${x.condition_key}|${x.condition}`))];
  return (
    <div className="mt-4 rounded-xl border border-line-soft bg-bg/40 p-3">
      <div className="label mb-2">{title} · par condition</div>
      <div className="-mx-1 overflow-x-auto">
        <table className="num w-full min-w-[480px] text-left text-xs">
          <tbody className="divide-y divide-line-soft">
            {groups.map((g) => {
              const [k, v] = g.split("|");
              const byH = new Map(rows.filter((x) => x.condition_key === k && x.condition === v).map((x) => [x.horizon, x]));
              const n = Math.max(...[...byH.values()].map((x) => x.n));
              return (
                <tr key={g}>
                  <td className="py-1.5 pr-2 font-sans text-muted">{KEY_FR[k] ?? k}</td>
                  <td className="pr-2 font-sans">{v}</td>
                  <td className="pr-2 text-faint">n {n}</td>
                  {hz.map((h) => {
                    const x = byH.get(h);
                    return (
                      <td key={h} className="py-1 text-right">
                        <span title={x ? `n = ${x.n} · p = ${x.p.toExponential(1)}` : ""} className={`inline-block rounded px-1.5 py-0.5 ${cellCls(x, x?.mean_excess_atr)}`}>{x ? fr2(x.mean_excess_atr) : "—"}</span>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-[11px] text-muted">
      <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-5 rounded bg-up/15 ring-1 ring-up/40" />retenu</span>
      <span className="flex items-center gap-1.5"><span className="text-up/80">+0,12</span>significatif mais pas retenu (coûts, stabilité ou taille)</span>
      <span className="flex items-center gap-1.5"><span className="text-muted">+0,05</span>indiscernable du hasard</span>
    </div>
  );
}

function Exits({ rows, labels, tfs }: { rows: ExitRow[]; labels: Record<string, string>; tfs: string[] }) {
  const [tf, setTf] = useState(tfs[0] ?? "5m");
  const setups = useMemo(() => [...new Set(rows.map((x) => x.setup))], [rows]);
  const sub = rows.filter((x) => x.timeframe === tf);
  return (
    <Card title="Règles de sortie" right={tfs.length > 1 ? <Segmented label="Unité de temps sorties" value={tf} options={tfs} onChange={setTf} /> : undefined}>
      <p className="mb-3 text-xs text-muted">Espérance par trade en R, après spread, glissement et commission. Même signal, seule la sortie change.</p>
      <div className="-mx-1 overflow-x-auto">
        <table className="num w-full min-w-[480px] text-left text-xs">
          <thead>
            <tr className="text-[10.5px] uppercase tracking-wider text-faint">
              <th className="pb-2 font-medium">Sortie</th>
              {setups.map((s) => <th key={s} className="pb-2 text-right font-medium">{SETUP_FR[s as Setup] ?? s}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-line-soft">
            {Object.keys(labels).map((rule) => (
              <tr key={rule}>
                <td className="py-2 pr-2 font-sans">{labels[rule]}</td>
                {setups.map((s) => {
                  const x = sub.find((y) => y.setup === s && y.rule === rule);
                  return (
                    <td key={s} className="py-1 text-right">
                      <span title={x?.expectancy_r != null ? `n = ${x.n} · réussite ${((x.win_rate ?? 0) * 100).toFixed(0)} % · IC [${fr2(x.ci?.[0])} ; ${fr2(x.ci?.[1])}]` : `n = ${x?.n ?? 0} (trop peu)`} className={`inline-block rounded px-1.5 py-0.5 ${cellCls(x, x?.expectancy_r)}`}>
                        {x?.expectancy_r != null ? `${fr2(x.expectancy_r)} R` : "—"}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-[11px] text-faint">Une case rouge « retenue » signifie une perte démontrée, pas un avantage : la combinaison est à écarter.</p>
    </Card>
  );
}
