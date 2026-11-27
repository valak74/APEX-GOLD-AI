"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BacktestForm } from "@/components/backtest/BacktestForm";
import { BacktestList } from "@/components/backtest/BacktestList";
import { BacktestReport } from "@/components/backtest/BacktestReport";
import { Header } from "@/components/Header";
import { Card } from "@/components/ui";
import { api, ApiError, type BacktestDetail, type BacktestListItem, type LiveQuote, type Lockout, type Me, type SystemStatus } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

export default function BacktestsPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [quote, setQuote] = useState<LiveQuote | null>(null);
  const [lockout, setLockout] = useState<Lockout | null>(null);
  const [items, setItems] = useState<BacktestListItem[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSlow = useCallback(async () => {
    try {
      const [m, s, l] = await Promise.all([api<Me>("/auth/me"), api<SystemStatus>("/system/status"), api<BacktestListItem[]>("/backtests")]);
      setMe(m); setStatus(s); setItems(l); setError(null);
      setLockout(await api<Lockout>("/calendar/lockout").catch(() => null));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Erreur inconnue");
    }
  }, [router]);
  usePoll(loadSlow, 5000);
  usePoll(async () => setQuote(await api<LiveQuote>("/market/quote").catch(() => null)), 2000);

  // default selection: newest run
  useEffect(() => {
    if (selected === null && items.length) setSelected(items[0].id);
  }, [items, selected]);

  // detail: (re)load when the selection changes or the listed status moves on
  const listed = items.find((b) => b.id === selected)?.status;
  useEffect(() => {
    if (selected === null) return;
    let live = true;
    api<BacktestDetail>(`/backtests/${selected}`)
      .then((d) => live && setDetail(d))
      .catch(() => live && setDetail(null));
    return () => {
      live = false;
    };
  }, [selected, listed]);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (!status || !me) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-muted">{error ? `Serveur indisponible : ${error}` : "Chargement…"}</p>
      </main>
    );
  }
  const canRun = me.role !== "viewer";

  return (
    <div className="min-h-screen">
      <Header status={status} me={me} quote={quote} dayOpen={null} lockout={lockout} onLogout={logout} onRefresh={loadSlow} />
      {error && <div className="bg-warn/10 px-6 py-1.5 text-center text-xs text-warn">Rafraîchissement interrompu : {error}</div>}
      <main className="mx-auto grid max-w-[1500px] gap-4 p-4 sm:p-6 xl:grid-cols-12">
        <aside className="space-y-4 xl:col-span-4">
          <BacktestForm
            canRun={canRun}
            onStarted={(id) => {
              setSelected(id);
              setDetail({ id, status: "running", error: null, request: items[0]?.request, report: null, created_at: "", finished_at: null } as BacktestDetail);
              void loadSlow();
            }}
          />
          <BacktestList items={items} selected={selected} onSelect={setSelected} />
        </aside>
        <div className="min-w-0 xl:col-span-8">
          {detail && detail.id === selected ? (
            <BacktestReport d={detail} />
          ) : (
            <Card title="Backtests">
              <div className="space-y-3 py-6 text-sm text-muted">
                <p>Testez une stratégie sur l&apos;historique stocké, avec les mêmes contrôles de risque qu&apos;en réel et des coûts prudents.</p>
                <p className="text-xs text-faint">
                  Une stratégie n&apos;est « validée » que si elle passe tous les critères fixés à l&apos;avance : échantillon suffisant, espérance positive
                  même au pire de l&apos;intervalle de confiance, résistance à des coûts +50 %, stabilité dans le temps et paramètres voisins rentables.
                </p>
              </div>
            </Card>
          )}
        </div>
      </main>
      <footer className="mx-auto max-w-[1500px] px-6 pb-8 text-center text-[11px] text-faint">
        APEX GOLD v{status.version} · les performances passées ne préjugent pas des performances futures
      </footer>
    </div>
  );
}
