"use client";

import { SETUP_FR, toDate, type BacktestListItem } from "@/lib/api";
import { Badge, Card } from "../ui";

export function BacktestList({ items, selected, onSelect }: { items: BacktestListItem[]; selected: number | null; onSelect: (id: number) => void }) {
  return (
    <Card title="Historique" right={<span className="num text-[11px] text-faint">{items.length}</span>} pad={false}>
      {items.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-faint">Aucun backtest pour l&apos;instant.</p>
      ) : (
        <ul className="max-h-[520px] divide-y divide-line-soft overflow-y-auto pb-2">
          {items.map((b) => (
            <li key={b.id}>
              <button
                onClick={() => onSelect(b.id)}
                aria-current={b.id === selected}
                className={`w-full px-5 py-3 text-left transition hover:bg-elev/50 ${b.id === selected ? "bg-elev/70" : ""}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">
                    {SETUP_FR[b.request.setup]} <span className="num text-xs text-faint">· {b.request.timeframe}</span>
                  </span>
                  <StatusBadge b={b} />
                </div>
                <div className="num mt-1 flex justify-between text-[11px] text-muted">
                  <span>#{b.id} · {toDate(b.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</span>
                  {b.status === "done" && (
                    <span>
                      {b.trades ?? 0} trades · {b.expectancy_r != null ? `${b.expectancy_r > 0 ? "+" : ""}${b.expectancy_r.toFixed(2)} R` : "—"}
                    </span>
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function StatusBadge({ b }: { b: BacktestListItem }) {
  if (b.status === "running") return <Badge tone="gold"><span className="pulse inline-block h-1.5 w-1.5 rounded-full bg-gold" />en cours</Badge>;
  if (b.status === "error") return <Badge tone="bad">erreur</Badge>;
  return b.validated ? <Badge tone="ok">✓ validée</Badge> : <Badge tone="neutral">✕ non validée</Badge>;
}
