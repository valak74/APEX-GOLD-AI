"use client";

import { fmtTime, type CalEvent, type Lockout } from "@/lib/api";
import { Badge, Card } from "./ui";

const IMP = {
  high: { label: "Fort", dot: "bg-danger" },
  medium: { label: "Moyen", dot: "bg-warn" },
  low: { label: "Faible", dot: "bg-faint" },
} as const;

export function CalendarCard({ lockout, events }: { lockout: Lockout | null; events: CalEvent[] }) {
  return (
    <Card title="Calendrier économique" right={lockout && (lockout.locked ? <Badge tone="bad">⏸ Trading bloqué</Badge> : <Badge tone="ok">✓ Pas d&apos;annonce imminente</Badge>)}>
      {lockout?.locked && (
        <div className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          {lockout.reason} — nouvelles entrées bloquées jusqu&apos;à {fmtTime(lockout.until)}
        </div>
      )}
      {!lockout?.locked && lockout?.next_event_title && (
        <p className="mb-3 text-sm text-muted">
          Prochain blocage : <span className="text-text">{lockout.next_event_title}</span> à {fmtTime(lockout.next_event_at, true)}
          <span className="text-faint"> (±{lockout.before_min} min)</span>
        </p>
      )}
      {events.length === 0 ? (
        <p className="text-sm text-faint">
          Aucun événement chargé. Importez le calendrier : <code className="num text-muted">apex calendar-import fichier.json</code>
        </p>
      ) : (
        <ul className="space-y-1">
          {events.slice(0, 8).map((e) => {
            const past = new Date(e.scheduled_at) < new Date();
            return (
              <li key={e.event_code + e.scheduled_at} className={`flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm ${past ? "opacity-50" : ""}`}>
                <span className="num w-24 shrink-0 text-xs text-muted">{fmtTime(e.scheduled_at, true)}</span>
                <span className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted" title={`Impact ${IMP[e.importance].label}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${IMP[e.importance].dot}`} />
                  {IMP[e.importance].label}
                </span>
                <span className="min-w-0 flex-1 truncate">{e.currency} · {e.title}</span>
                <span className="num hidden shrink-0 text-xs text-faint sm:inline">
                  {e.actual ? `réel ${e.actual}` : e.forecast ? `prév. ${e.forecast}` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
