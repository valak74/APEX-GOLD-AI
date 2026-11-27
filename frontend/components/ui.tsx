import type { ReactNode } from "react";

export function Card({ title, right, children, className = "", pad = true }: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  pad?: boolean;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 px-5 pt-4">
          {title ? <h2 className="label">{title}</h2> : <span />}
          {right}
        </header>
      )}
      <div className={pad ? "p-5 pt-3" : ""}>{children}</div>
    </section>
  );
}

export function Dot({ tone }: { tone: "ok" | "warn" | "bad" | "off" }) {
  const c = { ok: "bg-ok", warn: "bg-warn", bad: "bg-danger", off: "bg-faint" }[tone];
  return (
    <span className="relative inline-flex h-2 w-2">
      {tone === "ok" && <span className={`absolute inline-flex h-full w-full rounded-full ${c} opacity-50 pulse`} />}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${c}`} />
    </span>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "gold" | "ok" | "warn" | "bad" }) {
  const c = {
    neutral: "border-line text-muted",
    gold: "border-gold/40 text-gold bg-gold-soft",
    ok: "border-ok/40 text-ok bg-ok/10",
    warn: "border-warn/40 text-warn bg-warn/10",
    bad: "border-danger/40 text-danger bg-danger/10",
  }[tone];
  return <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${c}`}>{children}</span>;
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: readonly T[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-bg/60 p-0.5">
      {options.map((o) => (
        <button
          key={o}
          role="radio"
          aria-checked={o === value}
          onClick={() => onChange(o)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${o === value ? "bg-elev text-text shadow" : "text-muted hover:text-text"}`}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

/** Signed value with arrow — never colour alone. */
export function Delta({ value, suffix = "", digits = 2 }: { value: number | null; suffix?: string; digits?: number }) {
  if (value === null || Number.isNaN(value)) return <span className="text-muted">—</span>;
  const up = value > 0, down = value < 0;
  return (
    <span className={`num ${up ? "text-up" : down ? "text-down" : "text-muted"}`}>
      {up ? "▲ +" : down ? "▼ −" : ""}
      {Math.abs(value).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}
      {suffix}
    </span>
  );
}

export function Soon({ children }: { children: ReactNode }) {
  return <p className="text-sm text-faint">{children}</p>;
}
