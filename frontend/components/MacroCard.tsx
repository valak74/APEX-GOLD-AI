"use client";

import { toDate, type MacroContext } from "@/lib/api";
import { Card, Delta } from "./ui";

/** Known historical tendencies — shown as context, never as signals (tested in Recherche). */
const READING: Record<string, Record<string, { text: string; tone: "up" | "down" | "warn" }>> = {
  "taux réels (5 j)": {
    "en hausse": { text: "Taux réels en hausse : historiquement un vent contraire pour l'or.", tone: "down" },
    "en baisse": { text: "Taux réels en baisse : historiquement un soutien pour l'or.", tone: "up" },
  },
  "dollar (5 j)": {
    "en hausse": { text: "Dollar en hausse : l'or, coté en dollars, a tendance à en souffrir.", tone: "down" },
    "en baisse": { text: "Dollar en baisse : généralement favorable à l'or.", tone: "up" },
  },
  "spéculateurs (COT)": {
    "très acheteurs": { text: "Spéculateurs très acheteurs : positionnement chargé, risque de dégagement brutal.", tone: "warn" },
    "peu acheteurs": { text: "Spéculateurs peu exposés : de la marge pour acheter.", tone: "up" },
  },
  "jour d'annonce": { oui: { text: "Annonce majeure aujourd'hui : volatilité et blocage des entrées autour de l'heure.", tone: "warn" } },
};

export function MacroCard({ m }: { m: MacroContext | null }) {
  const right = <span className="text-[11px] text-faint">FRED · CFTC · données publiées uniquement</span>;
  if (!m) return <Card title="Contexte macro" right={right}><p className="text-sm text-faint">Chargement…</p></Card>;
  const readings = Object.entries(m.labels).map(([k, v]) => READING[k]?.[v]).filter(Boolean) as { text: string; tone: "up" | "down" | "warn" }[];
  return (
    <Card title="Contexte macro" right={right}>
      {!m.fred_configured && (
        <p className="mb-3 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn">
          Clé FRED absente : taux, dollar et calendrier officiel ne sont pas mis à jour. Ajoutez <code className="num">APEX_FRED_API_KEY</code> dans <code className="num">.env</code> (gratuite).
        </p>
      )}
      {m.series.length === 0 ? (
        <p className="text-sm text-faint">Aucune donnée macro pour l&apos;instant : lancez <code className="num text-muted">apex macro-sync</code> ou attendez la synchronisation automatique.</p>
      ) : (
        <div className="grid gap-x-6 sm:grid-cols-2">
          {m.series.filter((s) => s.key !== "COT_OI").map((s) => (
            <div key={s.key} className="flex items-baseline justify-between gap-3 border-b border-line-soft py-2">
              <div className="min-w-0">
                <div className="truncate text-sm">{s.label}</div>
                <div className="num text-[10.5px] text-faint">au {toDate(s.as_of).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" })}{s.percentile_3y != null && ` · ${Math.round(s.percentile_3y * 100)}e centile sur 3 ans`}</div>
              </div>
              <div className="text-right">
                <div className="num text-sm font-medium">
                  {s.unit === "contrats" ? Math.round(s.value).toLocaleString("fr-FR") : s.value.toLocaleString("fr-FR", { maximumFractionDigits: 2 })}
                  {s.unit === "%" ? " %" : ""}
                </div>
                <div className="text-[11px]">
                  <Delta value={s.change} digits={s.unit === "contrats" ? 0 : 2} suffix={s.unit === "%" ? " pt" : ""} /> <span className="text-faint">{s.change_window}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {readings.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {readings.map((r) => (
            <li key={r.text} className="flex gap-2 text-xs">
              <span aria-hidden className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${r.tone === "up" ? "bg-up" : r.tone === "down" ? "bg-down" : "bg-warn"}`} />
              <span className="text-muted">{r.text}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[10.5px] text-faint">Tendances historiques générales, pas des signaux : leur effet réel sur les setups est mesuré dans l&apos;onglet Recherche.</p>
    </Card>
  );
}
