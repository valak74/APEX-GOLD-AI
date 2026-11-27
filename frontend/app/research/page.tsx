"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { DiagnosticReport } from "@/components/research/DiagnosticReport";
import { Badge, Card } from "@/components/ui";
import { api, ApiError, toDate, type DiagDetail, type DiagListItem, type Holdout, type LiveQuote, type Lockout, type Me, type SystemStatus } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const TFS = ["5m", "15m", "1h"] as const;

export default function ResearchPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [quote, setQuote] = useState<LiveQuote | null>(null);
  const [lockout, setLockout] = useState<Lockout | null>(null);
  const [hold, setHold] = useState<Holdout | null>(null);
  const [items, setItems] = useState<DiagListItem[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<DiagDetail | null>(null);
  const [tfs, setTfs] = useState<string[]>([...TFS]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSlow = useCallback(async () => {
    try {
      const [m, s, l, h] = await Promise.all([
        api<Me>("/auth/me"),
        api<SystemStatus>("/system/status"),
        api<DiagListItem[]>("/research/diagnostics"),
        api<Holdout>("/research/holdout"),
      ]);
      setMe(m); setStatus(s); setItems(l); setHold(h); setError(null);
      setLockout(await api<Lockout>("/calendar/lockout").catch(() => null));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Erreur inconnue");
    }
  }, [router]);
  usePoll(loadSlow, 5000);
  usePoll(async () => setQuote(await api<LiveQuote>("/market/quote").catch(() => null)), 2000);

  useEffect(() => {
    if (selected === null && items.length) setSelected(items[0].id);
  }, [items, selected]);
  const listed = items.find((b) => b.id === selected)?.status;
  useEffect(() => {
    if (selected === null) return;
    let live = true;
    api<DiagDetail>(`/research/diagnostics/${selected}`).then((d) => live && setDetail(d)).catch(() => live && setDetail(null));
    return () => {
      live = false;
    };
  }, [selected, listed]);

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ id: number }>("/research/diagnostics", { method: "POST", body: JSON.stringify({ timeframes: tfs }) });
      setSelected(r.id);
      setDetail({ id: r.id, status: "running", error: null, report: null, created_at: "" });
      void loadSlow();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Échec du lancement");
    } finally {
      setBusy(false);
    }
  }

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
  const holdDay = hold?.start ? toDate(hold.start).toLocaleDateString("fr-FR", { day: "2-digit", month: "long", year: "numeric" }) : null;

  return (
    <div className="min-h-screen">
      <Header status={status} me={me} quote={quote} dayOpen={null} lockout={lockout} onLogout={logout} onRefresh={loadSlow} />
      {error && <div className="bg-warn/10 px-6 py-1.5 text-center text-xs text-warn">Rafraîchissement interrompu : {error}</div>}
      <main className="mx-auto grid max-w-[1500px] gap-4 p-4 sm:p-6 xl:grid-cols-12">
        <aside className="space-y-4 xl:col-span-4">
          <Card title="Période réservée" right={hold?.start ? <Badge tone="gold">verrouillée</Badge> : <Badge>en attente</Badge>}>
            {holdDay ? (
              <div className="space-y-2 text-sm">
                <p>
                  Données à partir du <span className="font-semibold text-gold">{holdDay}</span> mises de côté pour le verdict final.
                </p>
                <p className="text-xs text-muted">La recherche et les backtests ordinaires ne les voient jamais. Cette date ne bouge plus.</p>
                <p className="num text-xs text-faint">
                  Consultée {hold?.consultations ?? 0} fois{(hold?.consultations ?? 0) > 0 ? " — chaque consultation affaiblit sa valeur de preuve" : ""}
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted">Sera fixée automatiquement (6 derniers mois) dès qu&apos;au moins 60 jours d&apos;historique seront importés.</p>
            )}
          </Card>
          <Card title="Nouveau diagnostic" right={<span className="text-[11px] text-faint">aucun ordre · aucun réglage à optimiser</span>}>
            <div className="mb-1 text-xs text-muted">Unités de temps</div>
            <div className="flex gap-1.5">
              {TFS.map((t) => {
                const on = tfs.includes(t);
                return (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setTfs(on ? tfs.filter((x) => x !== t) : [...tfs, t])}
                    className={`num flex-1 rounded-lg border px-2 py-1.5 text-xs transition ${on ? "border-gold/60 bg-gold-soft text-text" : "border-line text-faint hover:text-muted"}`}
                  >
                    {t}
                  </button>
                );
              })}
            </div>
            <button
              onClick={run}
              disabled={!canRun || busy || tfs.length === 0}
              className="mt-4 w-full rounded-lg bg-gold py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "Lancement…" : "Lancer le diagnostic"}
            </button>
            {!canRun && <p className="mt-2 text-xs text-faint">Rôle « chercheur » ou plus requis.</p>}
            {err && <p className="mt-2 text-sm text-danger">{err}</p>}
          </Card>
          <Card title="Historique" right={<span className="num text-[11px] text-faint">{items.length}</span>} pad={false}>
            {items.length === 0 ? (
              <p className="px-5 pb-5 text-sm text-faint">Aucun diagnostic pour l&apos;instant.</p>
            ) : (
              <ul className="max-h-[420px] divide-y divide-line-soft overflow-y-auto pb-2">
                {items.map((b) => (
                  <li key={b.id}>
                    <button onClick={() => setSelected(b.id)} aria-current={b.id === selected} className={`w-full px-5 py-3 text-left transition hover:bg-elev/50 ${b.id === selected ? "bg-elev/70" : ""}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="num text-sm">#{b.id} · {b.request.timeframes.join(" · ")}</span>
                        {b.status === "running" ? <Badge tone="gold">en cours</Badge> : b.status === "error" ? <Badge tone="bad">erreur</Badge> : b.edges_total == null ? <Badge>terminé</Badge> : <Badge tone={b.edges_total > 0 ? "ok" : "neutral"}>{b.edges_total > 0 ? `${b.edges_total} avantage${b.edges_total > 1 ? "s" : ""}` : "aucun avantage"}</Badge>}
                      </div>
                      <div className="num mt-1 text-[11px] text-muted">
                        {toDate(b.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}
                        {b.tests_total != null && ` · ${b.tests_total} tests`}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </aside>
        <div className="min-w-0 xl:col-span-8">
          {detail && detail.id === selected ? (
            <DiagnosticReport d={detail} />
          ) : (
            <Card title="Recherche">
              <div className="space-y-3 py-6 text-sm text-muted">
                <p>Avant de construire une stratégie, mesurez ce qui prédit réellement le mouvement de l&apos;or : balayages, cassures, FVG, order blocks, selon la séance, le régime, la volatilité et la tendance de fond.</p>
                <p className="text-xs text-faint">Chaque élément est testé contre le hasard avec correction pour tests multiples, et comparé aux coûts réels. Les règles de sortie sont comparées sur les mêmes signaux.</p>
              </div>
            </Card>
          )}
        </div>
      </main>
      <footer className="mx-auto max-w-[1500px] px-6 pb-8 text-center text-[11px] text-faint">
        APEX GOLD v{status.version} · une corrélation passée n&apos;est pas une garantie
      </footer>
    </div>
  );
}
