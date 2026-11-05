"use client";

import { FormEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

type LoginResponse = {
  detail?: string;
  user?: { username: string; role: "approver" | "viewer" };
};

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = (await response.json()) as LoginResponse;
      if (!response.ok || !payload.user) {
        throw new Error(payload.detail || "Connexion impossible.");
      }

      const returnTo = searchParams.get("returnTo");
      const destination =
        returnTo?.startsWith("/") && !returnTo.startsWith("//") && returnTo !== "/login"
          ? returnTo
          : "/";
      router.replace(destination);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Connexion impossible.");
    } finally {
      setBusy(false);
    }
  }

  const serviceUnavailable = searchParams.get("error") === "auth-service";

  return (
    <main className="relative grid min-h-screen overflow-hidden bg-[#f3f5f0] text-[#172420] lg:grid-cols-[1.05fr_.95fr]">
      <div className="soft-grid pointer-events-none absolute inset-0 opacity-60" />
      <section className="relative hidden min-h-screen flex-col justify-between overflow-hidden bg-[#103c30] px-12 py-10 text-white lg:flex xl:px-20">
        <div className="absolute -right-32 -top-32 h-[440px] w-[440px] rounded-full border border-white/10" />
        <div className="absolute -right-12 -top-12 h-[280px] w-[280px] rounded-full border border-white/10" />
        <div className="relative flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-white text-xl font-semibold text-[#103c30]">r</div>
          <div>
            <div className="text-xl font-semibold tracking-[-.04em]">relay<span className="text-[#8fc8a1]">.</span></div>
            <div className="text-[9px] font-semibold uppercase tracking-[.2em] text-white/55">Agent communication studio</div>
          </div>
        </div>
        <div className="relative max-w-xl pb-10">
          <div className="mb-6 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[.22em] text-[#a6d7b3]">
            <span className="h-px w-7 bg-[#a6d7b3]" /> Simulation workspace
          </div>
          <h1 className="text-5xl font-semibold leading-[1.08] tracking-[-.06em] xl:text-6xl">
            Better service starts with a better conversation.
          </h1>
          <p className="mt-6 max-w-md text-sm leading-7 text-white/65">
            Sign in to run customer-call simulations, review conversations, and connect your advisor with a synthetic client.
          </p>
          <div className="mt-10 flex items-center gap-3 text-[10px] text-white/55">
            <span className="h-2 w-2 rounded-full bg-[#8fc8a1]" />
            Local demonstration environment · Synthetic data only
          </div>
        </div>
        <p className="relative text-[9px] uppercase tracking-[.16em] text-white/35">Cashy · Relay workspace</p>
      </section>

      <section className="relative flex min-h-screen items-center justify-center px-5 py-12 sm:px-8">
        <div className="w-full max-w-[410px]">
          <div className="mb-10 flex items-center gap-3 lg:hidden">
            <div className="grid h-10 w-10 place-items-center rounded-2xl bg-[#103c30] text-xl font-semibold text-white">r</div>
            <div>
              <div className="text-lg font-semibold tracking-[-.04em]">relay<span className="text-[#27634d]">.</span></div>
              <div className="text-[9px] font-semibold uppercase tracking-[.18em] text-[#89938d]">Simulation workspace</div>
            </div>
          </div>

          <div className="rounded-[28px] border border-[#e5e9e3] bg-white p-7 shadow-[0_24px_70px_rgba(27,46,37,0.08)] sm:p-10">
            <div className="mb-8">
              <p className="mb-3 text-[10px] font-semibold uppercase tracking-[.2em] text-[#27634d]">Welcome to Relay</p>
              <h2 className="text-[30px] font-semibold tracking-[-.055em]">Connect to your workspace</h2>
              <p className="mt-2 text-xs leading-5 text-[#748078]">Use your administrator account to continue to the simulation.</p>
            </div>

            {(error || serviceUnavailable) && (
              <div role="alert" className="mb-5 rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-[11px] leading-5 text-rose-700">
                {error || "Le service de connexion est indisponible. Vérifie que le Customer Advisor est démarré."}
              </div>
            )}

            <form onSubmit={submit} className="space-y-5">
              <label className="block">
                <span className="mb-2 block text-[10px] font-semibold text-[#46544b]">Nom d’utilisateur</span>
                <input
                  autoComplete="username"
                  autoFocus
                  required
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  className="w-full rounded-xl border border-[#e1e7e0] bg-[#fbfcfa] px-4 py-3 text-sm text-[#172420] outline-none transition placeholder:text-[#a3aca5] focus:border-[#6b9b7b] focus:bg-white"
                  placeholder="Votre identifiant"
                />
              </label>
              <label className="block">
                <span className="mb-2 block text-[10px] font-semibold text-[#46544b]">Mot de passe</span>
                <input
                  autoComplete="current-password"
                  required
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="w-full rounded-xl border border-[#e1e7e0] bg-[#fbfcfa] px-4 py-3 text-sm text-[#172420] outline-none transition placeholder:text-[#a3aca5] focus:border-[#6b9b7b] focus:bg-white"
                  placeholder="Votre mot de passe"
                />
              </label>
              <button
                type="submit"
                disabled={busy}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#103c30] px-4 py-3.5 text-xs font-semibold text-white transition hover:bg-[#0a3026] disabled:cursor-wait disabled:opacity-60"
              >
                {busy ? "Connexion…" : "Se connecter"}
                {!busy && <span aria-hidden="true">→</span>}
              </button>
            </form>

            <p className="mt-6 text-center text-[9px] leading-5 text-[#929c94]">
              Application de démonstration locale. Utilise uniquement des données synthétiques.
            </p>
          </div>
          <p className="mt-5 text-center text-[9px] text-[#98a199]">Accès réservé aux comptes Relay autorisés.</p>
        </div>
      </section>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className="grid min-h-screen place-items-center bg-[#f3f5f0] text-xs text-[#748078]">
          Loading…
        </main>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
