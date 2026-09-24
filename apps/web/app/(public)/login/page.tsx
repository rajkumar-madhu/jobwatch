"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Logo } from "@/components/brand";
import { setKey } from "@/lib/api";
import { authLoginHref } from "@/lib/auth-nav";

const ERROR_COPY: Record<string, string> = {
  sign_in_cancelled: "Sign-in was cancelled. Try again when you are ready.",
  sign_in_required: "Sign-in is required. Continue with your account below.",
  sign_in_failed: "Sign-in failed. Try again, or use an API key.",
  sign_in_incomplete: "Sign-in did not finish. Start again from this page.",
};

function LoginInner() {
  const router = useRouter();
  const params = useSearchParams();
  const authError = params.get("error");
  const [email, setEmail] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [mode, setMode] = useState<"account" | "key">("account");
  const errorMsg = authError ? (ERROR_COPY[authError] ?? ERROR_COPY.sign_in_failed) : null;

  const continueAccount = (e: React.FormEvent) => {
    e.preventDefault();
    window.location.assign(authLoginHref("/", email));
  };

  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="hero-band relative hidden overflow-hidden text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <Logo light />
        <div className="relative max-w-md">
          <h1 className="text-4xl font-semibold leading-tight">The desk for every job that has to run.</h1>
          <p className="mt-4 text-white/80">Sign in to see what is healthy, what is late, and what failed overnight.</p>
        </div>
        <p className="relative text-sm text-white/70">WeCrew JobWatch</p>
      </section>
      <section className="flex items-center justify-center bg-bg px-5 py-12">
        <div className="card w-full max-w-md p-8">
          <div className="mb-6 lg:hidden"><Logo /></div>
          <h2 className="text-2xl font-semibold text-accent">Sign in</h2>
          <p className="mt-1 text-sm text-mute">Use your organisation account, or an API key from Settings.</p>
          {errorMsg && <div className="mt-4 rounded-md border border-bad/40 bg-bad/5 px-4 py-3 text-sm text-bad" role="alert">{errorMsg}</div>}
          <div className="mt-5 grid grid-cols-2 rounded-full bg-bg p-1 text-sm font-medium" role="tablist" aria-label="Sign-in method">
            <button type="button" role="tab" aria-selected={mode === "account"} className={`rounded-full py-2 ${mode === "account" ? "bg-panel text-accent shadow-sm" : "text-mute"}`} onClick={() => setMode("account")}>Account</button>
            <button type="button" role="tab" aria-selected={mode === "key"} className={`rounded-full py-2 ${mode === "key" ? "bg-panel text-accent shadow-sm" : "text-mute"}`} onClick={() => setMode("key")}>API key</button>
          </div>
          {mode === "account" ? (
            <form className="mt-5 space-y-3" onSubmit={continueAccount}>
              <label className="block text-sm font-medium">Work email
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2.5" placeholder="you@company.com" autoComplete="email" />
              </label>
              <button className="btn btn-primary mt-2 w-full py-2.5" type="submit">Continue</button>
              <p className="text-xs text-mute">Optional — we pass it to your organisation’s sign-in page so the email is pre-filled.</p>
            </form>
          ) : (
            <form className="mt-5 space-y-3" onSubmit={(e) => { e.preventDefault(); setKey(apiKey.trim()); router.push("/"); }}>
              <label className="block text-sm font-medium">API key
                <input required value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2.5 font-mono text-sm" placeholder="cs_…" autoComplete="off" />
              </label>
              <button className="btn btn-primary w-full py-2.5" type="submit">Open dashboard</button>
              <p className="text-xs text-mute">Stored in this browser only.</p>
            </form>
          )}
          <p className="mt-6 text-sm text-mute">New here? <Link href="/signup" className="font-semibold text-accent">Create an account</Link></p>
        </div>
      </section>
    </main>
  );
}

export default function Login() {
  return <Suspense><LoginInner /></Suspense>;
}
