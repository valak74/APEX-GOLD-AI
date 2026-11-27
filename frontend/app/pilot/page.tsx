"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { Header } from "@/components/Header";
import { AutopilotCard } from "@/components/pilot/AutopilotCard";
import { AlertsCard, DivergenceCard, GateCard, JournalCard, StatsCard } from "@/components/pilot/Evidence";
import { api, ApiError, type LiveQuote, type Lockout, type Me, type PaperOrder, type PaperReport, type PaperStatus, type SystemStatus } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function PilotPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [quote, setQuote] = useState<LiveQuote | null>(null);
  const [lockout, setLockout] = useState<Lockout | null>(null);
  const [paper, setPaper] = useState<PaperStatus | null>(null);
  const [report, setReport] = useState<PaperReport | null>(null);
  const [rows, setRows] = useState<PaperOrder[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, s, p, r, j] = await Promise.all([
        api<Me>("/auth/me"),
        api<SystemStatus>("/system/status"),
        api<PaperStatus>("/paper/status"),
        api<PaperReport>("/paper/report"),
        api<PaperOrder[]>("/paper/journal?limit=300"),
      ]);
      setMe(m); setStatus(s); setPaper(p); setReport(r); setRows(j); setError(null);
      setLockout(await api<Lockout>("/calendar/lockout").catch(() => null));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Erreur inconnue");
    }
  }, [router]);
  usePoll(load, 5000);
  usePoll(async () => setQuote(await api<LiveQuote>("/market/quote").catch(() => null)), 2000);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (!status || !me || !paper || !report) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-muted">{error ? `Serveur indisponible : ${error}` : "Chargement…"}</p>
      </main>
    );
  }
  const trader = me.role === "trader" || me.role === "admin";

  return (
    <div className="min-h-screen">
      <Header status={status} me={me} quote={quote} dayOpen={null} lockout={lockout} onLogout={logout} onRefresh={load} />
      {status.kill_switch_engaged && (
        <div className="border-b border-danger/30 bg-danger/10 px-6 py-2 text-center text-sm text-danger">
          Arrêt d&apos;urgence engagé — {status.kill_switch_reason}. Le pilote est arrêté.
        </div>
      )}
      {error && <div className="bg-warn/10 px-6 py-1.5 text-center text-xs text-warn">Rafraîchissement interrompu : {error}</div>}
      <main className="mx-auto grid max-w-[1500px] gap-4 p-4 sm:p-6 xl:grid-cols-12">
        <aside className="space-y-4 xl:col-span-4">
          <AutopilotCard s={paper} canUse={trader} onChange={(p) => { setPaper(p); void load(); }} />
          <AlertsCard a={paper.alerts} canTest={trader} />
        </aside>
        <div className="min-w-0 space-y-4 xl:col-span-8">
          <GateCard r={report} />
          <StatsCard r={report} />
          <JournalCard rows={rows} />
          <DivergenceCard />
        </div>
      </main>
      <footer className="mx-auto max-w-[1500px] px-6 pb-8 text-center text-[11px] text-faint">
        APEX GOLD v{status.version} · mode PAPER : ordres simulés sur prix réels, aucun argent engagé
      </footer>
    </div>
  );
}
