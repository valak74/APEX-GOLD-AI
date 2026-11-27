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
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-7">
        <div className="mb-2 flex flex-col items-center text-center">
          <svg width="40" height="40" viewBox="0 0 32 32" aria-hidden="true">
            <defs>
              <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#f3d48a" />
                <stop offset="1" stopColor="#b8862e" />
              </linearGradient>
            </defs>
            <path d="M16 3 29 27H3Z" fill="url(#lg)" />
            <path d="M16 12 22 23H10Z" fill="#0a0c10" />
          </svg>
          <h1 className="mt-3 text-xl font-semibold tracking-wide">
            APEX <span className="text-gold">GOLD</span>
          </h1>
          <p className="mt-1 text-sm text-muted">Connexion sécurisée</p>
        </div>
        <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" />
        <Field label="Mot de passe" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
        <Field label="Code 2FA (si activé)" value={totp} onChange={setTotp} autoComplete="one-time-code" inputMode="numeric" />
        {error && <p className="text-sm text-down">{error}</p>}
        <button disabled={busy} className="w-full rounded-lg bg-gold py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:opacity-50">
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
        className="mt-1 w-full rounded-lg border border-line bg-bg/60 px-3 py-2.5 outline-none transition focus:border-gold"
      />
    </label>
  );
}
