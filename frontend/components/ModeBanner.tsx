import type { SystemStatus } from "@/lib/api";

const STYLES: Record<SystemStatus["mode"], { cls: string; label: string; text: string }> = {
  paper: { cls: "bg-paper text-black", label: "PAPER", text: "Ordres simulés sur prix réels — aucun argent réel engagé" },
  simulation: { cls: "bg-sim text-white", label: "SIMULATION", text: "Backtest sur données historiques" },
  live: { cls: "bg-down text-white", label: "LIVE", text: "ARGENT RÉEL — ordres envoyés au broker" },
};

export function ModeBanner({ mode }: { mode: SystemStatus["mode"] }) {
  const s = STYLES[mode];
  return (
    <div className={`${s.cls} flex items-center justify-center gap-3 px-4 py-1.5 text-sm font-semibold`} role="status">
      <span className="tracking-widest">{s.label}</span>
      <span className="font-normal opacity-90">· {s.text}</span>
    </div>
  );
}
