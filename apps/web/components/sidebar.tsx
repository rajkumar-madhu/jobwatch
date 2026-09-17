"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Overview } from "@/lib/api";

const NAV: [string, string][] = [
  ["/", "Overview"], ["/jobs", "Jobs"], ["/failures", "Failures"], ["/incidents", "Incidents"], ["/logs", "Logs"], ["/kubernetes", "Kubernetes"], ["/topology", "Topology"],
  ["/analytics", "Analytics"], ["/copilot", "AI Copilot"], ["/billing", "Billing"], ["/agents", "Servers & agents"], ["/alerting", "Alerting"], ["/settings", "Settings"],
];

function NavList({ bad, onNav }: { bad: number; onNav?: () => void }) {
  const path = usePathname();
  return (
    <nav className="flex-1 px-2">
      {NAV.map(([href, label]) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} onClick={onNav} className={clsx("flex items-center justify-between rounded-md px-2 py-1.5 text-sm", active ? "bg-accent/10 text-accent font-medium" : "text-ink/80 hover:bg-ink/5")}>
            {label}{href === "/failures" && bad > 0 && <span className="rounded-full bg-bad/15 px-1.5 text-[11px] font-medium text-bad">{bad}</span>}
          </Link>);
      })}
    </nav>
  );
}

export function Sidebar() {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);
  const { data } = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/v1/analytics/overview") });
  const bad = (data?.by_status.failed ?? 0) + (data?.by_status.missed ?? 0) + (data?.by_status.timeout ?? 0);
  const signOut = async () => { await fetch(`${process.env.NEXT_PUBLIC_API_URL}/auth/logout`, { method: "POST", credentials: "include" }); location.href = "/welcome"; };
  return (
    <>
      {/* mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-panel px-4 py-3 lg:hidden">
        <span className="flex items-center gap-2 font-semibold"><span className="dot dot-healthy" />WeCrew JobWatch</span>
        <button aria-label="Menu" onClick={() => setOpen(true)} className="btn px-2">☰{bad > 0 && <span className="ml-1 rounded-full bg-bad/15 px-1.5 text-[11px] text-bad">{bad}</span>}</button>
      </header>
      {open && <div className="fixed inset-0 z-40 bg-ink/40 lg:hidden" onClick={() => setOpen(false)}>
        <aside className="flex h-full w-64 flex-col bg-panel py-4" onClick={(e) => e.stopPropagation()}>
          <div className="mb-2 px-4 font-semibold">WeCrew JobWatch</div><NavList bad={bad} onNav={() => setOpen(false)} />
          <button className="px-4 py-3 text-left text-xs text-mute" onClick={signOut}>Sign out</button></aside></div>}
      {/* desktop */}
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-panel lg:flex">
        <div className="flex items-center gap-2 px-4 py-4"><span className="dot dot-healthy" /><span className="font-semibold tracking-tight">WeCrew JobWatch</span></div>
        <NavList bad={bad} />
        <div className="space-y-2 border-t border-line px-4 py-3 text-xs text-mute"><div><span className="kbd">⌘K</span> search & commands</div><button className="hover:text-ink" onClick={signOut}>Sign out</button></div>
      </aside>
    </>
  );
}
