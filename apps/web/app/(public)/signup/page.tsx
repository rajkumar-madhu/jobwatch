"use client";
import Link from "next/link";
import { useState } from "react";
import { Logo } from "@/components/brand";
import { authLoginHref } from "@/lib/auth-nav";

const ORG_KEY = "jw_pending_org";

export default function Signup() {
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);

  const start = (e: React.FormEvent) => {
    e.preventDefault();
    const name = company.trim().slice(0, 120);
    if (!name) return;
    try { sessionStorage.setItem(ORG_KEY, name); } catch { /* private mode */ }
    setDone(true);
    const next = `/onboarding?org=${encodeURIComponent(name)}`;
    window.location.assign(authLoginHref(next, email));
  };

  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="hero-band relative hidden overflow-hidden text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <Logo light />
        <div className="relative max-w-md">
          <h1 className="text-4xl font-semibold leading-tight">Start a workspace in a few minutes.</h1>
          <p className="mt-4 text-white/80">Name the company, sign in, and JobWatch is ready for the first heartbeat or agent.</p>
        </div>
        <p className="relative text-sm text-white/70">14-day Team trial on every new workspace.</p>
      </section>
      <section className="flex items-center justify-center bg-bg px-5 py-12">
        <div className="card w-full max-w-md p-8">
          <div className="mb-6 lg:hidden"><Logo /></div>
          <h2 className="text-2xl font-semibold text-accent">Create your account</h2>
          <p className="mt-1 text-sm text-mute">We’ll open secure sign-in, then use your company name for the workspace.</p>
          <form className="mt-5 space-y-3" onSubmit={start}>
            <label className="block text-sm font-medium">Company
              <input required value={company} onChange={(e) => setCompany(e.target.value)} className="mt-1 w-full rounded-xl border border-line px-3 py-2.5" placeholder="Acme" autoComplete="organization" />
            </label>
            <label className="block text-sm font-medium">Work email <span className="font-normal text-mute">(optional)</span>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1 w-full rounded-xl border border-line px-3 py-2.5" placeholder="you@company.com" autoComplete="email" />
            </label>
            <button className="btn btn-primary w-full py-2.5" type="submit" disabled={!company.trim()}>Continue to sign-in</button>
            {done && <p className="text-sm text-mute">Opening secure sign-in…</p>}
            <p className="text-xs text-mute">Your identity provider creates the account. We only keep the company name for workspace setup.</p>
          </form>
          <p className="mt-6 text-sm text-mute">Already have an account? <Link href="/login" className="font-semibold text-accent">Sign in</Link></p>
        </div>
      </section>
    </main>
  );
}
