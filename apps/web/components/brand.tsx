"use client";
import Link from "next/link";
import { useState } from "react";

export function Logo({ light = false }: { light?: boolean }) {
  return (
    <Link href="/welcome" className={`flex items-center gap-2.5 font-semibold tracking-tight ${light ? "text-white" : "text-accent"}`}>
      <span className={`grid h-9 w-9 place-items-center rounded-full ${light ? "bg-white/15" : "bg-accent"}`} aria-hidden>
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <circle cx="10" cy="10" r="7" stroke="white" strokeWidth="1.6" />
          <path d="M10 6.2V10l2.6 1.6" stroke="white" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </span>
      JobWatch
    </Link>
  );
}

const NAV = [
  ["Features", "/welcome#features"],
  ["Pricing", "/welcome#pricing"],
  ["About", "/welcome#about"],
  ["Contact", "/welcome#contact"],
] as const;

export function SiteHeader() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-panel">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3">
        <Logo />
        <nav className="hidden items-center gap-7 text-sm font-medium text-ink/80 md:flex" aria-label="Primary">
          {NAV.map(([label, href]) => (
            <a key={href} href={href} className="hover:text-accent">{label}</a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <Link href="/login" className="hidden px-3 py-2 text-sm font-medium text-accent sm:inline">Sign in</Link>
          <Link href="/signup" className="btn btn-primary px-5">Start free trial</Link>
          <button
            type="button"
            className="btn px-3 md:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((v) => !v)}
          >
            Menu
          </button>
        </div>
      </div>
      {open && (
        <nav id="mobile-nav" className="border-t border-line bg-panel px-5 py-3 md:hidden" aria-label="Mobile">
          <ul className="space-y-1 text-sm font-medium">
            {NAV.map(([label, href]) => (
              <li key={href}>
                <a href={href} className="block rounded-lg px-3 py-2 hover:bg-accent/5" onClick={() => setOpen(false)}>{label}</a>
              </li>
            ))}
            <li>
              <Link href="/login" className="block rounded-lg px-3 py-2 text-accent" onClick={() => setOpen(false)}>Sign in</Link>
            </li>
          </ul>
        </nav>
      )}
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="bg-accent text-white">
      <div className="mx-auto grid max-w-6xl gap-8 px-5 py-12 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <Logo light />
          <p className="mt-4 max-w-xs text-sm text-white/75">Scheduled-job monitoring for teams that need to know a cron, backup, or pipeline failed before their users do.</p>
        </div>
        <div>
          <h2 className="text-sm font-semibold">Product</h2>
          <ul className="mt-3 space-y-2 text-sm text-white/75">
            <li><a href="/welcome#features">Features</a></li>
            <li><a href="/welcome#pricing">Pricing</a></li>
            <li><Link href="/tools/cron">Cron checker</Link></li>
            <li><Link href="/status/demo">Status page</Link></li>
          </ul>
        </div>
        <div>
          <h2 className="text-sm font-semibold">Account</h2>
          <ul className="mt-3 space-y-2 text-sm text-white/75">
            <li><Link href="/login">Sign in</Link></li>
            <li><Link href="/signup">Create account</Link></li>
            <li><Link href="/onboarding">Onboarding</Link></li>
          </ul>
        </div>
        <div>
          <h2 className="text-sm font-semibold">WeCrew</h2>
          <p className="mt-3 text-sm text-white/75">WeCrew Technologies Private Limited</p>
          <p className="mt-2 text-sm text-white/75"><a href="mailto:hello@wecrew.in" className="hover:text-white">hello@wecrew.in</a></p>
          <p className="mt-2 text-sm text-white/75"><a href="tel:+919176772077" className="hover:text-white">+91 91767 72077</a></p>
        </div>
      </div>
      <div className="border-t border-white/15 px-5 py-4 text-center text-xs text-white/60">© {new Date().getFullYear()} WeCrew Technologies Private Limited. All rights reserved.</div>
    </footer>
  );
}
