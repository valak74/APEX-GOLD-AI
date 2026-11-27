"use client";

import { useEffect, useState } from "react";
import { api, type CostProfile } from "@/lib/api";
import { Badge, Card } from "../ui";

/** Broker spreads recorded live vs the historical source, by hour (UTC). */
export function CostCalibration() {
  const [p, setP] = useState<CostProfile | null>(null);
  useEffect(() => {
    api<CostProfile>("/macro/costs").then(setP).catch(() => setP(null));
  }, []);
  if (!p) return null;
  const max = Math.max(...p.hours.flatMap((h) => [h.live ?? 0, h.hist ?? 0]), 0.01);
  const progress = Math.min(1, p.live_bars / p.min_live_bars);
  return (
    <Card title="Calibrage des coûts" right={p.ratio != null ? <Badge tone="gold">× {p.ratio.toLocaleString("fr-FR")}</Badge> : <Badge>en collecte</Badge>}>
      <p className="text-xs text-muted">{p.note}.</p>
      {p.ratio == null && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-bg/60"><div className="h-full bg-gold" style={{ width: `${progress * 100}%` }} /></div>
          <div className="num mt-1 text-[10.5px] text-faint">{p.live_bars.toLocaleString("fr-FR")} / {p.min_live_bars.toLocaleString("fr-FR")} bougies 5m enregistrées en direct</div>
        </div>
      )}
      {(p.live_bars > 0 || p.hist_bars > 0) && (
        <>
          <div className="mt-4 flex h-20 items-end gap-[2px]" role="img" aria-label="Spread médian par heure : IC Markets contre Dukascopy">
            {p.hours.map((h) => (
              <div key={h.hour} className="flex h-full flex-1 items-end gap-[1px]" title={`${h.hour} h UTC · IC ${h.live ?? "—"} · Dukascopy ${h.hist ?? "—"}`}>
                <div className="flex-1 rounded-t-[2px] bg-gold/80" style={{ height: `${((h.live ?? 0) / max) * 100}%` }} />
                <div className="flex-1 rounded-t-[2px] bg-muted/50" style={{ height: `${((h.hist ?? 0) / max) * 100}%` }} />
              </div>
            ))}
          </div>
          <div className="num mt-1 flex justify-between text-[10px] text-faint"><span>0 h</span><span>12 h UTC</span><span>23 h</span></div>
          <div className="mt-2 flex gap-4 text-[11px] text-muted">
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-gold/80" />IC Markets (direct)</span>
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-muted/50" />Dukascopy (historique)</span>
          </div>
        </>
      )}
    </Card>
  );
}
