"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export function KillSwitch({ engaged, canUse, onDone }: { engaged: boolean; canUse: boolean; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  async function fire() {
    try {
      const r = await api<{ closed: unknown[]; errors: string[] }>("/system/kill-switch", {
        method: "POST",
        body: JSON.stringify({ reason, close_positions: true }),
      });
      setMsg(`Arrêt d'urgence activé — ${r.closed.length} position(s) fermée(s)${r.errors.length ? ` · erreurs : ${r.errors.join(", ")}` : ""}`);
      setOpen(false);
      onDone();
    } catch (e) {
      setMsg(`Échec : ${(e as Error).message}`);
    }
  }

  if (engaged) {
    return <span className="rounded-md border border-down px-3 py-1.5 text-sm font-semibold text-down">ARRÊT D&apos;URGENCE ACTIF</span>;
  }
  return (
    <div className="relative">
      <button
        disabled={!canUse}
        title={canUse ? "" : "Rôle trader requis"}
        onClick={() => setOpen(true)}
        className="rounded-md bg-down px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
      >
        Arrêt d&apos;urgence
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-2 w-72 space-y-2 rounded-lg border border-line bg-panel-2 p-3 shadow-xl">
          <p className="text-sm">Ferme toutes les positions et bloque le trading. Un admin devra réarmer.</p>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Raison (obligatoire)"
            className="w-full rounded border border-line bg-panel px-2 py-1 text-sm"
          />
          <div className="flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="px-2 py-1 text-sm text-muted">Annuler</button>
            <button disabled={reason.trim().length < 3} onClick={fire} className="rounded bg-down px-2 py-1 text-sm font-semibold text-white disabled:opacity-40">
              Confirmer
            </button>
          </div>
        </div>
      )}
      {msg && <p className="mt-1 max-w-72 text-xs text-muted">{msg}</p>}
    </div>
  );
}
