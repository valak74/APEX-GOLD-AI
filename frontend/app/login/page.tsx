"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/login", { method: "POST", body: JSON.stringify({ email, password, totp: totp || null }) });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429 ? "Trop de tentatives, réessayez dans une minute." : "Identifiants invalides.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-line bg-panel p-6">
        <div>
          <h1 className="text-xl font-semibold tracking-wide">
            <span className="text-gold">APEX</span> GOLD AI
          </h1>
          <p className="mt-1 text-sm text-muted">Connexion sécurisée</p>
        </div>
        <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" />
        <Field label="Mot de passe" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
        <Field label="Code 2FA (si activé)" value={totp} onChange={setTotp} autoComplete="one-time-code" inputMode="numeric" />
        {error && <p className="text-sm text-down">{error}</p>}
        <button disabled={busy} className="w-full rounded-md bg-gold py-2 font-medium text-black disabled:opacity-50">
          {busy ? "Connexion…" : "Se connecter"}
        </button>
      </form>
    </main>
  );
}

function Field(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  autoComplete?: string;
  inputMode?: "numeric";
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted">{props.label}</span>
      <input
        type={props.type ?? "text"}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        autoComplete={props.autoComplete}
        inputMode={props.inputMode}
        className="mt-1 w-full rounded-md border border-line bg-panel-2 px-3 py-2 outline-none focus:border-gold"
      />
    </label>
  );
}
