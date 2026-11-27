"use client";

import type { LiveQuote, SpecOut, SystemStatus } from "@/lib/api";

const STATE_FR: Record<SystemStatus["market_data"]["state"], string> = {
  disabled: "aucune source configurée",
  not_configured: "non configurée",
  connecting: "connexion…",
  connected: "connectée",
  reconnecting: "reconnexion…",
  error: "erreur",
};

export function LivePrice({ md, quote, spec }: { md: SystemStatus["market_data"]; quote: LiveQuote | null; spec: SpecOut | null }) {
  const stale = quote ? quote.age_s > 10 : true;
  return (
    <div className="space-y-3">
      {quote ? (
        <div className="flex flex-wrap items-end gap-6">
          <div>
            <div className="text-xs text-muted">Bid</div>
            <div className="num text-3xl text-down">{Number(quote.bid).toFixed(2)}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Ask</div>
            <div className="num text-3xl text-up">{Number(quote.ask).toFixed(2)}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Spread</div>
            <div className="num text-xl">{Number(quote.spread).toFixed(2)} $</div>
          </div>
          <div className={`text-xs ${stale ? "text-paper" : "text-muted"}`}>
            {stale ? "⚠ prix ancien : " : "mis à jour il y a "}
            {quote.age_s.toFixed(1)} s
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted">
          Aucun prix reçu. Source : {md.source === "none" ? "aucune (APEX_MARKET_DATA_SOURCE=none)" : `${md.source}, ${STATE_FR[md.state]}`}
          {md.error ? ` — ${md.error}` : ""}
        </p>
      )}
      {spec && (
        <p className="text-xs text-muted">
          Spécification {spec.source.startsWith("ctrader") ? `lue chez le broker (${spec.source.slice(8)})` : "PROVISOIRE (non lue chez le broker)"} :{" "}
          {spec.spec.contract_size} oz/lot · lot min {spec.spec.volume_min} · commission{" "}
          {spec.commission_per_lot_side_at_price ? `${Number(spec.commission_per_lot_side_at_price).toFixed(2)} ${spec.spec.commission_ccy}/lot/côté` : `${spec.spec.commission_rate} ${spec.spec.commission_ccy} (${spec.spec.commission_type})`}
          {spec.spec.margin_rate ? ` · marge ${(Number(spec.spec.margin_rate) * 100).toFixed(0)} %` : ""}
        </p>
      )}
    </div>
  );
}
