export function Panel({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-panel">
      <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">{title}</h2>
        {right}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  const color = tone === "up" ? "text-up" : tone === "down" ? "text-down" : "";
  return (
    <div className="rounded-lg bg-panel-2 px-3 py-2.5">
      <div className="text-xs text-muted">{label}</div>
      <div className={`num mt-0.5 text-lg ${color}`}>{value}</div>
    </div>
  );
}

export function Pending({ phase, what }: { phase: number; what: string }) {
  return (
    <p className="text-sm text-muted">
      {what} — <span className="text-text">pas encore construit</span> (phase {phase}). Aucune donnée simulée n&apos;est affichée.
    </p>
  );
}
