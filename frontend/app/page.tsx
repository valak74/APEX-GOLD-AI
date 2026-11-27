"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { AccountCard, PositionsCard, QualityCard, RiskCard } from "@/components/cards";
import { Header } from "@/components/Header";
import { PriceChart } from "@/components/PriceChart";
import { SizeCalculator } from "@/components/SizeCalculator";
import { Card, Soon } from "@/components/ui";
import {
  api,
  ApiError,
  type Account,
  type BarOut,
  type LiveQuote,
  type Me,
  type Position,
  type Quality,
  type RiskConfigOut,
  type SystemStatus,
} from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function Dashboard() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [risk, setRisk] = useState<RiskConfigOut | null>(null);
  const [quote, setQuote] = useState<LiveQuote | null>(null);
  const [quality, setQuality] = useState<Quality | null>(null);
  const [dayOpen, setDayOpen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSlow = useCallback(async () => {
    try {
      const [m, s, a, p, r] = await Promise.all([
        api<Me>("/auth/me"),
        api<SystemStatus>("/system/status"),
        api<Account>("/account"),
        api<Position[]>("/positions"),
        api<RiskConfigOut>("/risk/config"),
      ]);
      setMe(m); setStatus(s); setAccount(a); setPositions(p); setRisk(r); setError(null);
      const d = await api<BarOut[]>("/market/bars?timeframe=1d&limit=1").catch(() => []);
      setDayOpen(d.length ? Number(d[d.length - 1].open) : null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Erreur inconnue");
    }
  }, [router]);

  usePoll(loadSlow, 4000);
  usePoll(async () => {
    setQuote(await api<LiveQuote>("/market/quote").catch(() => null));
    setQuality(await api<Quality>("/market/quality").catch(() => null));
  }, 1000);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (!status || !account || !me) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-muted">{error ? `Serveur indisponible : ${error}` : "Chargement…"}</p>
      </main>
    );
  }
  const mid = quote ? (Number(quote.bid) + Number(quote.ask)) / 2 : null;

  return (
    <div className="min-h-screen">
      <Header status={status} me={me} quote={quote} dayOpen={dayOpen} onLogout={logout} onRefresh={loadSlow} />
      {status.kill_switch_engaged && (
        <div className="border-b border-danger/30 bg-danger/10 px-6 py-2 text-center text-sm text-danger">
          Arrêt d&apos;urgence engagé — {status.kill_switch_reason}. Trading bloqué jusqu&apos;au réarmement par un admin.
        </div>
      )}
      {error && <div className="bg-warn/10 px-6 py-1.5 text-center text-xs text-warn">Rafraîchissement interrompu : {error}</div>}

      <main className="mx-auto grid max-w-[1500px] gap-4 p-4 sm:p-6 xl:grid-cols-12">
        <div className="space-y-4 xl:col-span-8">
          <section className="card flex h-[460px] flex-col overflow-hidden sm:h-[min(64vh,580px)] sm:min-h-[440px]">
            <PriceChart />
          </section>
          <PositionsCard positions={positions} ccy={account.currency} />
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Analyse IA"><Soon>Régime de marché, scores et décision — phase 8. Rien n&apos;est simulé ici.</Soon></Card>
            <Card title="News & calendrier"><Soon>Flux d&apos;actualités et annonces économiques — phase 4.</Soon></Card>
          </div>
          {risk && <RiskCard risk={risk} equity={Number(account.equity.amount)} ccy={account.currency} />}
        </div>
        <aside className="space-y-4 xl:col-span-4">
          <AccountCard account={account} />
          <QualityCard q={quality} />
          <SizeCalculator accountCcy={account.currency} price={mid} />
        </aside>
      </main>
      <footer className="mx-auto max-w-[1500px] px-6 pb-8 text-center text-[11px] text-faint">
        APEX GOLD v{status.version} · aucune performance n&apos;est garantie · le mode {status.mode.toUpperCase()} n&apos;engage pas d&apos;argent réel
      </footer>
    </div>
  );
}
