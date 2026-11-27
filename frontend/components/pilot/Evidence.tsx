"use client";

import { useState } from "react";
import { api, ApiError, SETUP_FR, toDate, type AlertsStatus, type Divergence, type PaperOrder, type PaperReport, type Setup } from "@/lib/api";
import { Badge, Card, Segmented } from "../ui";

const sgn = (v: number | null | undefined, d = 2, suf = "") =>
  v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d })}${suf}`;
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted");
const STATUS: Record<PaperOrder["status"], { label: string; tone: "neutral" | "gold" | "ok" | "warn" | "bad" }> = {
  skipped: { label: "ignorée", tone: "neutral" },
  rejected: { label: "refusée", tone: "warn" },
  failed: { label: "échec", tone: "bad" },
  open: { label: "ouverte", tone: "gold" },
  closed: { label: "clôturée", tone: "ok" },
};
const REASON_FR: Record<string, string> = {
  stop_loss: "stop", take_profit: "objectif", time_exit: "durée max", weekly_close: "vendredi", kill_switch: "arrêt d'urgence", stop_out: "stop-out", manual: "manuelle",
  risk_reward: "rendement/risque", trading_enabled: "trading inactif", market_open: "marché fermé", data_quality: "qualité des données", spread: "spread",
  open_positions: "position déjà ouverte", news_lockout: "annonce", daily_loss: "perte du jour", losing_streak: "série de pertes", trades_per_day: "trades du jour",
  stop_distance: "distance du stop", duplicate: "déjà exécutée", quote_fresh: "prix trop ancien", rollover: "rollover", weekly_close_: "vendredi",
};
const fmtT = (s: string | null) => (s ? toDate(s).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "—");

export function GateCard({ r }: { r: PaperReport }) {
  return (
    <Card title="Preuve PAPER : critères avant tout passage en réel" right={r.gate_passed ? <Badge tone="ok">✓ critères remplis</Badge> : <Badge>en cours</Badge>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {r.gate.map((g) => (
          <div key={g.name} className="rounded-xl border border-line-soft bg-bg/40 p-3">
            <div className="flex items-start justify-between gap-2">
              <span className="text-sm">{g.name}</span>
              <span aria-hidden className={`text-xs font-bold ${g.passed ? "text-ok" : "text-faint"}`}>{g.passed ? "✓" : "…"}</span>
            </div>
            <div className="num mt-1 text-[11px] text-muted">{g.value} <span className="text-faint">· exigé {g.requirement}</span></div>
            {g.progress != null && (
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-bg/60">
                <div className={`h-full rounded-full ${g.passed ? "bg-ok" : "bg-gold"}`} style={{ width: `${g.progress * 100}%` }} />
              </div>
            )}
          </div>
        ))}
      </div>
      <p className="mt-3 text-[11px] text-faint">Même si tous les critères sont remplis, le passage en réel reste une décision humaine, à risque minimal (0,1–0,25 % par trade).</p>
    </Card>
  );
}

export function StatsCard({ r }: { r: PaperReport }) {
  const o = r.overall;
  return (
    <section className="card overflow-hidden">
      <header className="flex items-center justify-between px-5 pt-4">
        <h2 className="label">Résultats PAPER</h2>
        <span className="num text-[11px] text-faint">{r.first_at ? `depuis le ${toDate(r.first_at).toLocaleDateString("fr-FR")} · ${r.days} j` : "pas encore démarré"}</span>
      </header>
      <div className="mt-3 grid grid-cols-2 border-t border-line-soft sm:grid-cols-4">
        <Tile k="Trades clôturés" v={String(o.trades)} />
        <Tile k="Espérance / trade" v={sgn(o.expectancy_r, 2, " R")} cls={tone(o.expectancy_r)} sub={o.expectancy_r_ci?.[0] != null ? `IC [${sgn(o.expectancy_r_ci[0])} ; ${sgn(o.expectancy_r_ci[1])}]` : undefined} />
        <Tile k="Résultat net" v={o.net_profit != null ? `${sgn(o.net_profit, 2)} ${o.currency ?? ""}` : "—"} cls={tone(o.net_profit)} />
        <Tile k="Réussite" v={o.win_rate != null ? `${(o.win_rate * 100).toFixed(0)} %` : "—"} sub={o.profit_factor != null ? `PF ${o.profit_factor.toFixed(2)}` : undefined} />
        <Tile k="Glissement moyen" v={o.avg_slippage != null ? `${sgn(o.avg_slippage, 3)} $` : "—"} sub="> 0 = défavorable" />
        <Tile k="Délai moyen" v={o.avg_latency_s != null ? `${o.avg_latency_s.toFixed(0)} s` : "—"} sub="clôture bougie → exécution" />
        <Tile k="Sorties" v={o.by_reason ? Object.entries(o.by_reason).map(([k, n]) => `${REASON_FR[k] ?? k} ${n}`).join(" · ") : "—"} small />
        <Tile k="Groupes" v={String(r.groups.length)} sub="stratégie × unité" />
      </div>
      {r.groups.length > 0 && (
        <div className="overflow-x-auto px-5 py-4">
          <table className="num w-full min-w-[640px] text-left text-xs">
            <thead>
              <tr className="text-[10.5px] uppercase tracking-wider text-faint">
                {["Stratégie", "Idées", "Ouvertes / clôturées", "Espérance PAPER", "Backtest (IC 95 %)", "Cohérent"].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-line-soft">
              {r.groups.map((g) => (
                <tr key={`${g.setup}-${g.timeframe}`}>
                  <td className="py-2 font-sans">{SETUP_FR[g.setup]} <span className="text-faint">{g.timeframe}</span></td>
                  <td>{g.considered}</td>
                  <td>{g.status_counts.open ?? 0} / {g.status_counts.closed ?? 0}</td>
                  <td className={tone(g.stats.expectancy_r)}>{sgn(g.stats.expectancy_r, 2, " R")}</td>
                  <td className="text-muted">{g.backtest ? `${sgn(g.backtest.expectancy_r, 2, " R")} [${sgn(g.backtest.expectancy_r_ci[0])} ; ${sgn(g.backtest.expectancy_r_ci[1])}]` : "aucun backtest"}</td>
                  <td>{g.backtest?.paper_inside_ci == null ? <span className="text-faint">≥ 30 trades requis</span> : g.backtest.paper_inside_ci ? <span className="text-ok">✓ oui</span> : <span className="text-danger">✕ écart</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Tile({ k, v, sub, cls = "", small = false }: { k: string; v: string; sub?: string; cls?: string; small?: boolean }) {
  return (
    <div className="border-b border-r border-line-soft p-4 [&:nth-child(2n)]:border-r-0 sm:[&:nth-child(2n)]:border-r sm:[&:nth-child(4n)]:border-r-0">
      <div className="text-[11px] text-muted">{k}</div>
      <div className={`num mt-1 font-semibold ${small ? "text-xs leading-relaxed" : "text-xl"} ${cls}`}>{v}</div>
      {sub && <div className="num mt-0.5 text-[10.5px] text-faint">{sub}</div>}
    </div>
  );
}

export function JournalCard({ rows }: { rows: PaperOrder[] }) {
  const [f, setF] = useState<string>("toutes");
  const filters = { toutes: null, ouvertes: "open", clôturées: "closed", refusées: "rejected", ignorées: "skipped" } as const;
  const key = filters[f as keyof typeof filters];
  const shown = key ? rows.filter((r) => r.status === key) : rows;
  return (
    <Card title="Journal du pilote" right={<Segmented label="Filtre du journal" value={f} options={Object.keys(filters)} onChange={setF} />} pad={false}>
      <div className="max-h-[520px] overflow-auto px-5 pb-4">
        {shown.length === 0 ? (
          <p className="py-6 text-sm text-faint">Aucune entrée. Le journal se remplit dès que le pilote examine des idées.</p>
        ) : (
          <table className="num w-full min-w-[760px] text-left text-xs">
            <thead className="sticky top-0 bg-panel">
              <tr className="text-[10.5px] uppercase tracking-wider text-faint">
                {["Signal", "Stratégie", "Sens", "Statut", "Motif", "Exécution", "Glissement", "Sortie", "Net", "R"].map((h) => <th key={h} className="py-2 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-line-soft">
              {shown.map((r) => (
                <tr key={r.id}>
                  <td className="py-1.5 text-muted">{fmtT(r.signal_at)}</td>
                  <td className="font-sans">{SETUP_FR[r.setup as Setup] ?? r.setup} <span className="text-faint">{r.timeframe}</span></td>
                  <td className={r.direction === "bullish" ? "text-up" : "text-down"}>{r.direction === "bullish" ? "▲ Achat" : "▼ Vente"}</td>
                  <td><Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge></td>
                  <td className="max-w-[220px] truncate font-sans text-muted" title={r.reasons.join(", ")}>
                    {r.status === "closed" ? REASON_FR[r.close_reason ?? ""] ?? r.close_reason : r.reasons.map((x) => REASON_FR[x] ?? x).join(", ") || "—"}
                  </td>
                  <td>{r.fill_price ? `${Number(r.volume)} lot @ ${Number(r.fill_price).toFixed(2)}` : "—"}</td>
                  <td className={r.slippage ? tone(-Number(r.slippage)) : "text-faint"}>{r.slippage ? sgn(Number(r.slippage), 2) : "—"}</td>
                  <td className="text-muted">{r.close_price ? Number(r.close_price).toFixed(2) : "—"}</td>
                  <td className={tone(r.net_pnl ? Number(r.net_pnl) : null)}>{r.net_pnl ? sgn(Number(r.net_pnl), 2) : "—"}</td>
                  <td className={tone(r.r_multiple)}>{sgn(r.r_multiple, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  );
}

export function DivergenceCard() {
  const [setup, setSetup] = useState<string>("Balayage");
  const [tf, setTf] = useState<string>("5m");
  const [d, setD] = useState<Divergence | null>(null);
  const [busy, setBusy] = useState(false);
  const key = setup === "Balayage" ? "liquidity_sweep_reversal" : "trend_pullback";
  async function run() {
    setBusy(true);
    try {
      setD(await api<Divergence>(`/paper/divergence?setup=${key}&timeframe=${tf}`));
    } catch (e) {
      setD({ error: e instanceof ApiError ? e.message : "échec" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Réel contre backtest" right={<span className="text-[11px] text-faint">le backtest rejoué sur les prix enregistrés en direct</span>}>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Stratégie" value={setup} options={["Balayage", "Repli"]} onChange={setSetup} />
        <Segmented label="Unité" value={tf} options={["5m", "15m"]} onChange={setTf} />
        <button onClick={run} disabled={busy} className="ml-auto rounded-lg border border-line px-3 py-1.5 text-xs font-medium hover:bg-elev disabled:opacity-40">{busy ? "Calcul…" : "Comparer"}</button>
      </div>
      {d?.error && <p className="mt-3 text-sm text-muted">{d.error}</p>}
      {d && !d.error && (
        <div className="mt-4 space-y-3">
          <div className="num grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
            <Mini k="Accord" v={d.agreement != null ? `${(d.agreement * 100).toFixed(0)} %` : "—"} />
            <Mini k="Signaux communs" v={String(d.matched?.length ?? 0)} />
            <Mini k="Backtest seul" v={String(d.backtest_only?.length ?? 0)} />
            <Mini k="Direct seul" v={String(d.live_only?.length ?? 0)} warn={(d.live_only?.length ?? 0) > 0} />
          </div>
          <p className="text-[11px] text-faint">{d.note}</p>
        </div>
      )}
    </Card>
  );
}

function Mini({ k, v, warn = false }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className="rounded-xl border border-line-soft bg-bg/40 p-2">
      <div className={`text-base font-semibold ${warn ? "text-warn" : ""}`}>{v}</div>
      <div className="font-sans text-[10.5px] text-muted">{k}</div>
    </div>
  );
}

export function AlertsCard({ a, canTest }: { a: AlertsStatus; canTest: boolean }) {
  const [msg, setMsg] = useState<string | null>(null);
  async function test() {
    try {
      const r = await api<{ sent: boolean; error: string | null }>("/paper/alerts/test", { method: "POST" });
      setMsg(r.sent ? "Message envoyé ✓" : `Échec : ${r.error}`);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "échec");
    }
  }
  return (
    <Card title="Alertes" right={a.configured ? <Badge tone="ok">Telegram</Badge> : <Badge>non configurées</Badge>}>
      {!a.configured && (
        <p className="text-xs text-muted">
          Recevez ouvertures, clôtures, arrêts d&apos;urgence et coupures de flux sur votre téléphone : créez un bot avec @BotFather, puis ajoutez <code className="num text-text">APEX_TELEGRAM_BOT_TOKEN</code> et <code className="num text-text">APEX_TELEGRAM_CHAT_ID</code> dans <code className="num">.env</code>.
        </p>
      )}
      {a.configured && (
        <div className="flex items-center justify-between gap-3">
          <span className="num text-xs text-muted">{a.sent ?? 0} envoyées · {a.failed ?? 0} échecs</span>
          <button onClick={test} disabled={!canTest} className="rounded-lg border border-line px-3 py-1.5 text-xs hover:bg-elev disabled:opacity-40">Tester</button>
        </div>
      )}
      {msg && <p className="mt-2 text-xs text-muted">{msg}</p>}
      <ul className="mt-3 max-h-56 space-y-2 overflow-y-auto">
        {a.recent.length === 0 ? (
          <li className="text-xs text-faint">Aucune alerte récente.</li>
        ) : (
          a.recent.map((m, i) => (
            <li key={i} className="rounded-lg bg-bg/40 px-3 py-2 text-xs">
              <div className="num text-[10px] text-faint">{toDate(m.at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" })}</div>
              <div className="whitespace-pre-line text-muted">{m.text}</div>
            </li>
          ))
        )}
      </ul>
    </Card>
  );
}
