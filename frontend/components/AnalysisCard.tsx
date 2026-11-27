"use client";

import { REGIME_FR, type Context } from "@/lib/api";
import { Badge, Card } from "./ui";

const ORDER = ["1d", "4h", "1h", "15m", "5m"] as const;
const ROLE_FR = { higher: "Fond", intermediate: "Structure", execution: "Exécution" } as const;
const ZONE_FR: Record<string, string> = { premium: "Premium", discount: "Discount", equilibrium: "Équilibre" };

function Trend({ t }: { t: "up" | "down" | "none" }) {
  if (t === "up") return <span className="text-up">▲ Haussière</span>;
  if (t === "down") return <span className="text-down">▼ Baissière</span>;
  return <span className="text-faint">— Indéterminée</span>;
}

export function AnalysisCard({ ctx }: { ctx: Context | null }) {
  if (!ctx) {
    return (
      <Card title="Analyse de marché">
        <p className="text-sm text-faint">Calcul en cours… (nécessite des bougies : flux en direct ou historique importé)</p>
      </Card>
    );
  }
  const exec = ctx.timeframes["5m"] ?? ctx.timeframes["15m"];
  const pct = ((ctx.bias_score + 1) / 2) * 100;
  const biasFr = { bullish: "Haussier", bearish: "Baissier", neutral: "Neutre" }[ctx.bias];
  return (
    <Card
      title="Analyse de marché"
      right={<span className="text-[11px] text-faint">règles déterministes · mise à jour {new Date(ctx.generated_at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}</span>}
    >
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <div>
          <div className="text-xs text-muted">Biais multi-unités de temps{!ctx.htf_available && " (court terme uniquement)"}</div>
          <div className={`mt-1 text-2xl font-semibold ${ctx.bias === "bullish" ? "text-up" : ctx.bias === "bearish" ? "text-down" : "text-text"}`}>
            {ctx.bias === "bullish" ? "▲ " : ctx.bias === "bearish" ? "▼ " : ""}
            {biasFr}
            <span className="num ml-2 text-sm font-normal text-muted">{ctx.bias_score > 0 ? "+" : ""}{ctx.bias_score.toFixed(2)}</span>
          </div>
          {/* diverging meter: bearish ← neutral → bullish */}
          <div className="relative mt-3 h-1.5 rounded-full bg-gradient-to-r from-down/70 via-elev to-up/70" aria-hidden>
            <span className="absolute top-1/2 h-3.5 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-text shadow" style={{ left: `${pct}%` }} />
          </div>
          <div className="mt-1 flex justify-between text-[10px] text-faint"><span>baissier</span><span>neutre</span><span>haussier</span></div>
          <div className="mt-4 flex flex-wrap gap-2">
            {exec && <Badge tone="gold">{REGIME_FR[exec.regime.label] ?? exec.regime.label}</Badge>}
            {exec && exec.regime.volatility === "high" && <Badge tone="warn">Volatilité élevée</Badge>}
            {!ctx.htf_available ? (
              <Badge tone="warn">Fond 1d/4h indisponible</Badge>
            ) : ctx.htf_conflict ? (
              <Badge tone="warn">⚠ Exécution contre le fond</Badge>
            ) : (
              <Badge tone="neutral">{ctx.alignment === "aligned" ? "Unités alignées" : "Unités mixtes"}</Badge>
            )}
          </div>
          {exec?.regime.reasons?.[0] && <p className="mt-3 text-xs leading-relaxed text-muted">{exec.regime.reasons[0]}</p>}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[360px] text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-faint">
                <th className="py-1.5 pr-2 font-medium">UT</th>
                <th className="pr-2 font-medium">Tendance</th>
                <th className="pr-2 font-medium">Régime</th>
                <th className="pr-2 font-medium">Zone</th>
                <th className="text-right font-medium">RSI</th>
              </tr>
            </thead>
            <tbody>
              {ORDER.filter((tf) => ctx.timeframes[tf]).map((tf) => {
                const a = ctx.timeframes[tf];
                return (
                  <tr key={tf} className="border-t border-line-soft">
                    <td className="py-2 pr-2">
                      <span className="num font-medium">{tf}</span>
                      <span className="ml-1.5 text-[10px] text-faint">{ROLE_FR[a.role]}</span>
                    </td>
                    <td className="pr-2"><Trend t={a.effective_trend} /></td>
                    <td className="pr-2 text-muted">{REGIME_FR[a.regime.label] ?? a.regime.label}</td>
                    <td className="pr-2 text-muted">{a.premium_discount ? ZONE_FR[a.premium_discount.zone] : "—"}</td>
                    <td className="num text-right text-muted">{a.indicators.rsi?.toFixed(0) ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <p className="mt-4 text-[11px] text-faint">Contexte descriptif — pas un signal d&apos;achat ou de vente. Les statistiques de chaque motif sont mesurées sur l&apos;historique avant tout usage.</p>
    </Card>
  );
}
