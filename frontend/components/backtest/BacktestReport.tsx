"use client";

import { useState, type ReactNode } from "react";
import { REGIME_FR, SETUP_FR, toDate, type BacktestDetail, type BTReport, type BTSummary, type Group } from "@/lib/api";
import { Badge, Card, Segmented } from "../ui";
import { EquityChart } from "./EquityChart";

const CHECK_FR: Record<string, string> = {
  mode: "Mode de trading", rollover: "Rollover quotidien", weekly_close: "Fermeture du vendredi", quote_fresh: "Prix récent",
  spread: "Spread trop large", daily_loss: "Perte du jour", weekly_loss: "Perte de la semaine", drawdown: "Drawdown total",
  trades_per_day: "Trades par jour", losing_streak: "Série de pertes", open_positions: "Positions ouvertes",
  stop_distance: "Distance du stop (ATR)", risk_reward: "Rendement/risque après coûts", sizing: "Taille calculable",
  news_lockout: "Annonce économique", market_open: "Marché fermé", data_quality: "Qualité des données",
};
const EXIT_FR: Record<string, string> = {
  target: "Objectif", stop: "Stop", stop_gap: "Stop (gap)", time: "Durée max", weekly_close: "Fermeture hebdo", end_of_data: "Fin des données",
};
const SESSION_FR: Record<string, string> = { asia: "Asie", london: "Londres", newyork: "New York", late: "Fin de séance" };
const DIR_FR: Record<string, string> = { bullish: "Achat", bearish: "Vente" };

const n = (v: number | null | undefined, d = 2, suffix = "") =>
  v == null || Number.isNaN(v) ? "—" : `${v.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d })}${suffix}`;
const signed = (v: number | null | undefined, d = 2, suffix = "") => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${n(Math.abs(v), d, suffix)}`);
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted");
const day = (s: string) => toDate(s).toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });

export function BacktestReport({ d }: { d: BacktestDetail }) {
  if (d.status === "running") {
    return (
      <Card>
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <span className="pulse h-3 w-3 rounded-full bg-gold" />
          <p className="text-sm">Backtest #{d.id} en cours…</p>
          <p className="max-w-md text-xs text-muted">
            Simulation de base, coûts +50 %, walk-forward, 6 variantes de paramètres et Monte Carlo. Comptez de quelques secondes à quelques minutes selon l&apos;historique.
          </p>
        </div>
      </Card>
    );
  }
  if (d.status === "error" || !d.report) {
    return (
      <Card title={`Backtest #${d.id}`}>
        <p className="text-sm text-danger">✕ {d.error ?? "Rapport indisponible"}</p>
        {d.error?.includes("insuffisant") && (
          <p className="mt-3 text-xs text-muted">
            Importez d&apos;abord l&apos;historique : <code className="num rounded bg-elev px-1.5 py-0.5 text-text">apex import-dukascopy --from 2023-01-01 --to 2026-11-01</code>
          </p>
        )}
      </Card>
    );
  }
  return <Report d={d} r={d.report} />;
}

function Report({ d, r }: { d: BacktestDetail; r: BTReport }) {
  const s = r.summary;
  const initial = d.request.initial_balance;
  const v = r.validation;
  return (
    <div className="space-y-4">
      <section className={`card overflow-hidden ${v.validated ? "ring-1 ring-ok/40" : ""}`}>
        <div className="flex flex-wrap items-start justify-between gap-4 p-5">
          <div>
            <div className="label">Backtest #{d.id}</div>
            <h2 className="mt-1 text-lg font-semibold">
              {SETUP_FR[d.request.setup]} <span className="num text-sm font-normal text-muted">· {r.data.timeframe}</span>
            </h2>
            {r.data.final_holdout && <div className="mt-1"><Badge tone="gold">Verdict final · période réservée</Badge></div>}
            <p className="num mt-1 text-xs text-muted">
              {day(r.data.start)} → {day(r.data.end)} · {r.data.bars.toLocaleString("fr-FR")} bougies · source {r.data.sources.join(", ") || "—"}
            </p>
          </div>
          <div className="text-right">
            {v.validated ? (
              <Badge tone="ok">✓ Stratégie validée</Badge>
            ) : (
              <Badge tone="warn">✕ Non validée — ne pas trader</Badge>
            )}
            <p className="mt-2 max-w-xs text-[11px] text-faint">
              {v.validated
                ? "Tous les critères pré-enregistrés sont remplis. Prochaine étape : plusieurs semaines en PAPER."
                : "Au moins un critère pré-enregistré échoue. Aucun avantage démontré."}
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 border-t border-line-soft sm:grid-cols-4">
          <Tile k="Espérance / trade" v={signed(s.expectancy_r, 3, " R")} cls={tone(s.expectancy_r)} sub={s.expectancy_r_ci ? `IC 95 % [${n(s.expectancy_r_ci[0], 2)} ; ${n(s.expectancy_r_ci[1], 2)}]` : undefined} />
          <Tile k="Résultat net" v={signed(s.net_profit, 0, " $")} cls={tone(s.net_profit)} sub={s.return_pct != null ? `${signed(s.return_pct, 1, " %")} du capital` : undefined} />
          <Tile k="Drawdown max" v={n(s.max_drawdown_pct, 1, " %")} cls={(s.max_drawdown_pct ?? 0) > 10 ? "text-down" : ""} sub={s.max_drawdown != null ? `${n(s.max_drawdown, 0)} $` : undefined} />
          <Tile k="Trades" v={String(s.trades)} sub={`${r.signals} signaux · ${Object.values(r.rejected_by_risk_engine).reduce((a, b) => a + b, 0)} refusés`} />
          <Tile k="Taux de réussite" v={n((s.win_rate ?? NaN) * 100, 1, " %")} sub={s.win_rate_ci ? `IC [${n(s.win_rate_ci[0] * 100, 0)} ; ${n(s.win_rate_ci[1] * 100, 0)}] %` : undefined} />
          <Tile k="Profit factor" v={n(s.profit_factor, 2)} cls={s.profit_factor != null ? (s.profit_factor >= 1 ? "text-up" : "text-down") : ""} sub={`gain moy. ${n(s.avg_win_r, 2)} R · perte ${n(s.avg_loss_r, 2)} R`} />
          <Tile k="Sharpe / Sortino" v={`${n(s.sharpe, 2)} / ${n(s.sortino, 2)}`} sub="annualisés, sur jours tradés" />
          <Tile k="Coûts payés" v={`${n(s.total_costs, 0)} $`} sub={`séries : ${s.max_win_streak ?? 0} gains · ${s.max_loss_streak ?? 0} pertes`} />
        </div>
        {(r.rejected_by_risk_engine.drawdown ?? 0) > 0 && (
          <p className="border-t border-danger/20 bg-danger/10 px-5 py-2.5 text-xs text-danger">
            Le drawdown maximal autorisé a été atteint{r.trades.length ? ` le ${day(r.trades[r.trades.length - 1].exit_t)}` : ""} : le moteur de risque a
            bloqué tous les signaux suivants ({r.rejected_by_risk_engine.drawdown}), exactement comme il le ferait en réel.
          </p>
        )}
      </section>

      {r.equity.length > 1 && (
        <Card title="Courbe de capital" right={<span className="text-[11px] text-faint">capital · drawdown (%)</span>}>
          <EquityChart equity={r.equity} drawdown={r.drawdown} initial={initial} />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Critères de validation" right={<span className="text-[11px] text-faint">fixés avant le test</span>}>
          <ul className="space-y-2.5">
            {v.items.map((it) => (
              <li key={it.name} className="flex items-start gap-3">
                <span aria-hidden className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${it.passed ? "bg-ok/15 text-ok" : "bg-danger/15 text-danger"}`}>
                  {it.passed ? "✓" : "✕"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm">{it.name}<span className="sr-only">{it.passed ? " : réussi" : " : échoué"}</span></div>
                  <div className="num text-[11px] text-muted">{it.value} <span className="text-faint">· exigé {it.requirement}</span></div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Robustesse">
          <dl className="space-y-3 text-sm">
            <Compare k="Coûts +50 %" base={s.expectancy_r} other={r.stress_costs_150.expectancy_r} note={`${r.stress_costs_150.trades} trades`} />
            <Compare k="Hors échantillon (walk-forward)" base={s.expectancy_r} other={r.walk_forward.oos.expectancy_r} note={`${r.walk_forward.oos.trades} trades jamais vus à l'optimisation`} />
          </dl>
          <div className="mt-4 rounded-xl border border-line-soft bg-bg/40 p-3">
            <div className="label mb-2">Monte Carlo · ordre des trades mélangé</div>
            {r.monte_carlo.runs ? (
              <div className="num grid grid-cols-3 gap-2 text-center">
                <Mini k="DD médian" v={n(r.monte_carlo.max_dd_median_pct, 1, " %")} />
                <Mini k="DD 95e pct" v={n(r.monte_carlo.max_dd_p95_pct, 1, " %")} />
                <Mini k={`P(DD ≥ ${r.monte_carlo.prob_dd_over_pct} %)`} v={n((r.monte_carlo.prob_dd_over ?? 0) * 100, 1, " %")} />
              </div>
            ) : (
              <p className="text-xs text-faint">Pas assez de trades.</p>
            )}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Walk-forward" right={<span className="text-[11px] text-faint">paramètres choisis sur le passé uniquement</span>}>
          <Table head={["Période", "Dates", "Trades", "Espérance", "Net"]}>
            {r.folds.map((f, i) => {
              const p = r.walk_forward.periods.find((x) => x.period === i);
              return (
                <tr key={i}>
                  <td className="py-1.5">
                    {i + 1}
                    {i === 0 ? <span className="ml-1 text-[10px] text-faint">entraînement</span> : p?.params ? <span className="ml-1 text-[10px] text-faint">marge {p.params.stop_buffer_atr} · q≥{p.params.min_quality}</span> : <span className="ml-1 text-[10px] text-faint">—</span>}
                  </td>
                  <td className="text-muted">{f.from ? `${day(f.from)}` : "—"}</td>
                  <td>{f.trades}</td>
                  <td className={tone(f.expectancy_r)}>{signed(f.expectancy_r, 2, " R")}</td>
                  <td className={tone(f.net_profit)}>{signed(f.net_profit, 0, " $")}</td>
                </tr>
              );
            })}
          </Table>
        </Card>
        <Card title="Sensibilité aux paramètres" right={<span className="text-[11px] text-faint">un avantage réel ne dépend pas d&apos;un réglage précis</span>}>
          <Table head={["Marge stop", "Qualité min.", "Trades", "Espérance", "PF"]}>
            {r.sensitivity.map((x) => {
              const cur = x.stop_buffer_atr === d.request.stop_buffer_atr && x.min_quality === d.request.min_quality;
              return (
                <tr key={`${x.stop_buffer_atr}-${x.min_quality}`} className={cur ? "bg-gold-soft" : ""}>
                  <td className="py-1.5">{x.stop_buffer_atr} ATR{cur && <span className="ml-1 text-[10px] text-gold">testé</span>}</td>
                  <td>≥ {x.min_quality}</td>
                  <td>{x.trades}</td>
                  <td className={tone(x.expectancy_r)}>{signed(x.expectancy_r, 2, " R")}</td>
                  <td>{n(x.profit_factor, 2)}</td>
                </tr>
              );
            })}
          </Table>
        </Card>
      </div>

      {s.monthly && Object.keys(s.monthly).length > 0 && <Monthly monthly={s.monthly} initial={initial} />}
      {s.by && <Breakdown by={s.by} rejected={r.rejected_by_risk_engine} />}
      <Trades r={r} />
      <Assumptions r={r} />
    </div>
  );
}

function Tile({ k, v, sub, cls = "" }: { k: string; v: string; sub?: string; cls?: string }) {
  return (
    <div className="border-b border-r border-line-soft p-4 [&:nth-child(2n)]:border-r-0 sm:[&:nth-child(2n)]:border-r sm:[&:nth-child(4n)]:border-r-0">
      <div className="text-[11px] text-muted">{k}</div>
      <div className={`num mt-1 text-xl font-semibold ${cls}`}>{v}</div>
      {sub && <div className="num mt-0.5 text-[10.5px] text-faint">{sub}</div>}
    </div>
  );
}

function Mini({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <div className="text-base font-semibold">{v}</div>
      <div className="font-sans text-[10.5px] text-muted">{k}</div>
    </div>
  );
}

function Compare({ k, base, other, note }: { k: string; base?: number; other?: number; note: string }) {
  const ok = other != null && other > 0;
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <dt>{k}</dt>
        <dd className="text-[11px] text-faint">{note}</dd>
      </div>
      <div className="num text-right">
        <span className={tone(other)}>{signed(other, 3, " R")}</span>
        <div className="text-[11px] text-faint">base {signed(base, 3, " R")} {ok ? "· tient" : "· ne tient pas"}</div>
      </div>
    </div>
  );
}

function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="num w-full min-w-[360px] text-left text-xs">
        <thead>
          <tr className="text-[10.5px] uppercase tracking-wider text-faint">
            {head.map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-line-soft">{children}</tbody>
      </table>
    </div>
  );
}

function Monthly({ monthly, initial }: { monthly: Record<string, number>; initial: number }) {
  const entries = Object.entries(monthly);
  const max = Math.max(...entries.map(([, x]) => Math.abs(x)), 1);
  const pos = entries.filter(([, x]) => x > 0).length;
  return (
    <Card title="Résultat par mois" right={<span className="num text-[11px] text-faint">{pos}/{entries.length} mois positifs</span>}>
      <div className="flex h-36 items-stretch gap-[3px]" role="img" aria-label={`Résultat mensuel : ${pos} mois positifs sur ${entries.length}`}>
        {entries.map(([m, x]) => {
          const h = (Math.abs(x) / max) * 50;
          const label = toDate(`${m}-01T00:00:00Z`).toLocaleDateString("fr-FR", { month: "short", year: "2-digit" });
          return (
            <div key={m} className="group relative flex min-w-[6px] flex-1 flex-col" title={`${label} : ${signed(x, 0, " $")} (${signed((x / initial) * 100, 2, " %")})`}>
              <div className="flex flex-1 items-end">{x > 0 && <div className="w-full rounded-t-[3px] bg-up/80 group-hover:bg-up" style={{ height: `${h * 2}%` }} />}</div>
              <div className="h-px bg-line" />
              <div className="flex flex-1 items-start">{x < 0 && <div className="w-full rounded-b-[3px] bg-down/80 group-hover:bg-down" style={{ height: `${h * 2}%` }} />}</div>
            </div>
          );
        })}
      </div>
      <div className="num mt-2 flex justify-between text-[10.5px] text-faint">
        <span>{entries[0][0]}</span>
        <span>{entries[entries.length - 1][0]}</span>
      </div>
    </Card>
  );
}

const BY_TABS = { session: "Séance", regime: "Régime", direction: "Sens", exit: "Sortie" } as const;
type ByKey = keyof typeof BY_TABS;

function Breakdown({ by, rejected }: { by: NonNullable<BTSummary["by"]>; rejected: Record<string, number> }) {
  const [tab, setTab] = useState<string>(BY_TABS.session);
  const key = (Object.keys(BY_TABS) as ByKey[]).find((k) => BY_TABS[k] === tab) ?? "session";
  const fr = (k: string) => ({ session: SESSION_FR, regime: REGIME_FR, direction: DIR_FR, exit: EXIT_FR }[key][k] ?? k);
  const rows = Object.entries(by[key] ?? {}).sort((a, b) => b[1].n - a[1].n) as [string, Group][];
  const rej = Object.entries(rejected).sort((a, b) => b[1] - a[1]);
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <Card title="Répartition" right={<Segmented label="Répartition" value={tab} options={Object.values(BY_TABS)} onChange={setTab} />} className="lg:col-span-3">
        <Table head={[BY_TABS[key], "Trades", "Réussite", "Espérance"]}>
          {rows.map(([k, g]) => (
            <tr key={k}>
              <td className="py-1.5 font-sans">{fr(k)}</td>
              <td>{g.n}</td>
              <td>{n(g.win_rate * 100, 0, " %")}</td>
              <td className={tone(g.avg_r)}>{signed(g.avg_r, 2, " R")}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Refus du moteur de risque" right={<span className="text-[11px] text-faint">mêmes contrôles qu&apos;en réel</span>} className="lg:col-span-2">
        {rej.length === 0 ? (
          <p className="text-sm text-faint">Aucun signal refusé.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {rej.map(([code, c]) => (
              <li key={code} className="flex justify-between gap-3">
                <span className="text-muted">{CHECK_FR[code] ?? code}</span>
                <span className="num">{c}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function Trades({ r }: { r: BTReport }) {
  const [all, setAll] = useState(false);
  const list = [...r.trades].reverse();
  const shown = all ? list : list.slice(0, 25);
  return (
    <Card title="Trades" right={<span className="num text-[11px] text-faint">{r.trades.length < r.summary.trades ? `${r.trades.length} derniers sur ${r.summary.trades}` : `${r.trades.length}`}</span>} pad={false}>
      <div className="max-h-[560px] overflow-auto px-5 pb-4">
        <table className="num w-full min-w-[720px] text-left text-xs">
          <thead className="sticky top-0 bg-panel">
            <tr className="text-[10.5px] uppercase tracking-wider text-faint">
              {["Entrée", "Sens", "Régime", "Prix", "Stop", "Objectif", "Sortie", "Motif", "Lots", "Net", "R"].map((h) => (
                <th key={h} className="py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line-soft">
            {shown.map((t, i) => (
              <tr key={`${t.entry_t}-${i}`}>
                <td className="py-1.5 text-muted">{toDate(t.entry_t).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</td>
                <td className={t.direction === "bullish" ? "text-up" : "text-down"}>{t.direction === "bullish" ? "▲ Achat" : "▼ Vente"}</td>
                <td className="font-sans text-muted">{REGIME_FR[t.regime] ?? t.regime}</td>
                <td>{n(t.entry, 2)}</td>
                <td className="text-muted">{n(t.stop, 2)}</td>
                <td className="text-muted">{n(t.target, 2)}</td>
                <td>{n(t.exit, 2)}</td>
                <td className="font-sans text-muted">{EXIT_FR[t.reason] ?? t.reason}</td>
                <td>{t.volume}</td>
                <td className={tone(t.net)}>{signed(t.net, 2)}</td>
                <td className={tone(t.r)}>{signed(t.r, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.length > 25 && (
          <button onClick={() => setAll(!all)} className="mt-3 text-xs text-muted hover:text-text">
            {all ? "Réduire" : `Afficher les ${list.length} trades`}
          </button>
        )}
        {list.length === 0 && <p className="py-4 text-sm text-faint">Aucun trade.</p>}
      </div>
    </Card>
  );
}

function Assumptions({ r }: { r: BTReport }) {
  const a = r.assumptions;
  return (
    <Card title="Hypothèses de simulation">
      <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
        <Row k="Exécution" v={a.fills} />
        <Row k="Spread" v={`${a.spread} × ${r.costs.spread_mult}`} />
        <Row k="Glissement" v={`${r.costs.slippage} $/once à chaque exécution`} />
        <Row k="Commission" v={`${r.spec.commission} (spécification ${r.spec.source})`} />
        <Row k="Swap" v={a.swap} warn={a.swap.startsWith("non")} />
        <Row k="Annonces" v={`${r.data.news_windows} fenêtres de blocage appliquées`} warn={r.data.news_windows === 0} />
        <Row k="Devise" v={`${a.currency} (pas de conversion)`} />
        <Row k="Config. de risque" v={r.risk_config_hash.slice(0, 12)} />
      </dl>
      <p className="mt-4 text-[11px] leading-relaxed text-faint">
        Un backtest décrit le passé avec des hypothèses prudentes ; il ne garantit aucun résultat futur. Une stratégie validée doit encore
        faire ses preuves plusieurs semaines en mode PAPER avant toute activation en réel.
      </p>
    </Card>
  );
}

function Row({ k, v, warn = false }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b border-line-soft pb-1.5">
      <dt className="text-muted">{k}</dt>
      <dd className={`text-right ${warn ? "text-warn" : ""}`}>{v}</dd>
    </div>
  );
}
