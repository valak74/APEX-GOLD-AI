"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export function KillSwitch({ engaged, canUse, onDone }: { engaged: boolean; canUse: boolean; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function fire() {
    setBusy(true);
    try {
      const r = await api<{ closed: unknown[]; errors: string[] }>("/system/kill-switch", {
        method: "POST",
        body: JSON.stringify({ reason, close_positions: true }),
      });
      setMsg(`${r.closed.length} position(s) fermée(s)${r.errors.length ? ` · erreurs : ${r.errors.join(", ")}` : ""}`);
      setOpen(false);
      onDone();
    } catch (e) {
      setMsg(`Échec : ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  if (engaged) {
    return <span className="rounded-lg border border-danger/50 bg-danger/10 px-3 py-1.5 text-xs font-semibold tracking-wide text-danger">ARRÊT D&apos;URGENCE ACTIF</span>;
  }
  return (
    <>
      <button
        disabled={!canUse}
        title={canUse ? "Fermer toutes les positions et bloquer le trading" : "Rôle trader requis"}
        onClick={() => setOpen(true)}
        className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-1.5 text-xs font-semibold text-danger transition hover:bg-danger hover:text-white disabled:opacity-40"
      >
        Arrêt d&apos;urgence
      </button>
      {msg && <span className="hidden text-xs text-muted lg:inline">{msg}</span>}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="ks-title">
          <div className="card w-full max-w-md p-6">
            <h3 id="ks-title" className="text-lg font-semibold">Arrêt d&apos;urgence</h3>
            <p className="mt-2 text-sm text-muted">
              Toutes les positions seront fermées et le trading bloqué. Seul un admin (mot de passe + 2FA) pourra réarmer.
            </p>
            <input
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Raison (obligatoire)"
              className="mt-4 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-danger"
            />
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setOpen(false)} className="rounded-lg px-3 py-2 text-sm text-muted hover:text-text">Annuler</button>
              <button
                disabled={reason.trim().length < 3 || busy}
                onClick={fire}
                className="rounded-lg bg-danger px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
              >
                {busy ? "Arrêt…" : "Confirmer l'arrêt"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
