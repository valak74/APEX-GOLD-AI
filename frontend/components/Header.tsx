"use client";

import { useEffect, useRef, useState } from "react";
import type { LiveQuote, Me, SystemStatus } from "@/lib/api";
import { KillSwitch } from "./KillSwitch";
import { Dot } from "./ui";

const MODE = {
  paper: { label: "PAPER", cls: "border-warn/50 text-warn bg-warn/10", hint: "Ordres simulés sur prix réels" },
  simulation: { label: "SIMULATION", cls: "border-sky-400/50 text-sky-300 bg-sky-400/10", hint: "Backtest" },
  live: { label: "LIVE", cls: "border-danger/60 text-danger bg-danger/15", hint: "Argent réel" },
} as const;

export function Header({ status, me, quote, dayOpen, onLogout, onRefresh }: {
  status: SystemStatus;
  me: Me;
  quote: LiveQuote | null;
  dayOpen: number | null;
  onLogout: () => void;
  onRefresh: () => void;
}) {
  const mid = quote ? (Number(quote.bid) + Number(quote.ask)) / 2 : null;
  const prev = useRef<number | null>(null);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    if (mid !== null && prev.current !== null && mid !== prev.current) {
      setFlash(mid > prev.current ? "flash-up" : "flash-down");
      const id = setTimeout(() => setFlash(""), 600);
      prev.current = mid;
      return () => clearTimeout(id);
    }
    prev.current = mid;
  }, [mid]);

  const chg = mid !== null && dayOpen ? mid - dayOpen : null;
  const chgPct = chg !== null && dayOpen ? (chg / dayOpen) * 100 : null;
  const md = status.market_data;
  const stale = !quote || quote.age_s > 10;
  const mode = MODE[status.mode];
  const canTrade = me.role === "trader" || me.role === "admin";

  return (
    <header className="sticky top-0 z-40 border-b border-line/80 bg-bg/75 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <Logo />
          <span className="text-[15px] font-semibold tracking-wide">
            APEX <span className="text-gold">GOLD</span>
          </span>
          <span title={mode.hint} className={`rounded-md border px-2 py-0.5 text-[11px] font-bold tracking-[0.14em] ${mode.cls}`}>
            {mode.label}
          </span>
        </div>

        <div className="order-3 flex w-full items-baseline gap-4 sm:order-none sm:w-auto sm:flex-1 sm:justify-center">
          {mid !== null ? (
            <>
              <span className="text-xs text-muted">XAU/USD</span>
              <span className={`num text-2xl font-semibold tracking-tight ${flash} ${stale ? "text-muted" : ""}`}>
                {mid.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
              {chg !== null && chgPct !== null && (
                <span className={`num text-sm ${chg > 0 ? "text-up" : chg < 0 ? "text-down" : "text-muted"}`} title="Variation depuis l'ouverture de la séance (18:00 New York)">
                  {chg > 0 ? "▲ +" : chg < 0 ? "▼ −" : ""}
                  {Math.abs(chg).toFixed(2)} ({Math.abs(chgPct).toFixed(2)} %)
                </span>
              )}
              <span className="num hidden text-xs text-muted md:inline">spread {Number(quote!.spread).toFixed(2)}</span>
              {stale && <span className="text-xs text-warn">prix figé</span>}
            </>
          ) : (
            <span className="text-sm text-faint">XAU/USD — en attente du flux</span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-4">
          <div className="hidden items-center gap-3 text-xs text-muted lg:flex">
            <span className="flex items-center gap-1.5" title={md.error ?? ""}>
              <Dot tone={md.state === "connected" ? (stale ? "warn" : "ok") : md.source === "none" ? "off" : "bad"} />
              Données
            </span>
            <span className="flex items-center gap-1.5">
              <Dot tone={status.broker.connected ? "ok" : "bad"} />
              Broker
            </span>
            <span className="flex items-center gap-1.5">
              <Dot tone={status.trading_enabled ? "ok" : "off"} />
              Trading {status.trading_enabled ? "actif" : "inactif"}
            </span>
          </div>
          <KillSwitch engaged={status.kill_switch_engaged} canUse={canTrade} onDone={onRefresh} />
          <button onClick={onLogout} className="group flex items-center gap-2 text-xs text-muted hover:text-text" title="Déconnexion">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-elev text-[11px] font-semibold uppercase text-text">
              {me.email.slice(0, 2)}
            </span>
            <span className="hidden sm:inline">Quitter</span>
          </button>
        </div>
      </div>
    </header>
  );
}

function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#f3d48a" />
          <stop offset="1" stopColor="#b8862e" />
        </linearGradient>
      </defs>
      <path d="M16 3 29 27H3Z" fill="url(#g)" />
      <path d="M16 12 22 23H10Z" fill="#0a0c10" />
    </svg>
  );
}
