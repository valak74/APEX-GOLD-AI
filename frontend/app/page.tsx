"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { AnalysisCard } from "@/components/AnalysisCard";
import { CalendarCard } from "@/components/CalendarCard";
import { AccountCard, PositionsCard, QualityCard } from "@/components/cards";
import { RiskPanel } from "@/components/RiskPanel";
import { Header } from "@/components/Header";
import { OpportunitiesList, OpportunityDetail } from "@/components/Opportunities";
import { PriceChart } from "@/components/PriceChart";
import { SizeCalculator } from "@/components/SizeCalculator";
import {
  api,
  ApiError,
  type Account,
  type BarOut,
  type CalEvent,
  type Context,
  type Lockout,
  type Opportunity,
  type LiveQuote,
  type Me,
  type Position,
  type Quality,
  type RiskConfigOut,
  type RiskStatus,
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
  const [ctx, setCtx] = useState<Context | null>(null);
  const [lockout, setLockout] = useState<Lockout | null>(null);
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [riskStatus, setRiskStatus] = useState<RiskStatus | null>(null);

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
    setRiskStatus(await api<RiskStatus>("/risk/status").catch(() => null));
  }, 1000);

  usePoll(async () => {
    setCtx(await api<Context>("/analysis/context").catch(() => null));
    setLockout(await api<Lockout>("/calendar/lockout").catch(() => null));
    setEvents(await api<CalEvent[]>("/calendar/upcoming").catch(() => []));
  }, 20000);

  usePoll(async () => {
    const list = await api<Opportunity[]>("/opportunities").catch(() => null);
    if (list) setOpps(list);
  }, 10000);
  const selected = opps.find((o) => o.id === selectedId) ?? null;

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
      <Header status={status} me={me} quote={quote} dayOpen={dayOpen} lockout={lockout} onLogout={logout} onRefresh={loadSlow} />
      {status.kill_switch_engaged && (
        <div className="border-b border-danger/30 bg-danger/10 px-6 py-2 text-center text-sm text-danger">
          Arrêt d&apos;urgence engagé — {status.kill_switch_reason}. Trading bloqué jusqu&apos;au réarmement par un admin.
        </div>
      )}
      {error && <div className="bg-warn/10 px-6 py-1.5 text-center text-xs text-warn">Rafraîchissement interrompu : {error}</div>}

      <main className="mx-auto grid max-w-[1500px] gap-4 p-4 sm:p-6 xl:grid-cols-12">
        <div className="space-y-4 xl:col-span-8">
          <section className="card flex h-[460px] flex-col overflow-hidden sm:h-[min(64vh,580px)] sm:min-h-[440px]">
            <PriceChart focus={selected} onClearFocus={() => setSelectedId(null)} />
          </section>
          {selected && (
            <OpportunityDetail
              o={selected}
              canExplain={(me.role === "trader" || me.role === "admin") && status.llm_enabled}
              canEvaluate={me.role === "trader" || me.role === "admin"}
              onClose={() => setSelectedId(null)}
              onExplained={(n) => setOpps((xs) => xs.map((x) => (x.id === selected.id ? { ...x, narrative: n } : x)))}
            />
          )}
          <PositionsCard positions={positions} ccy={account.currency} />
          <AnalysisCard ctx={ctx} />
          <CalendarCard lockout={lockout} events={events} />
        </div>
        <aside className="space-y-4 xl:col-span-4">
          <OpportunitiesList items={opps} selected={selectedId} onSelect={(o) => setSelectedId(o?.id ?? null)} />
          <AccountCard account={account} />
          <RiskPanel risk={riskStatus} cfg={risk} me={me} tradingEnabled={status.trading_enabled} killSwitch={status.kill_switch_engaged} onChange={loadSlow} />
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
