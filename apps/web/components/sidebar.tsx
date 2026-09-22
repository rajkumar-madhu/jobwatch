"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Overview } from "@/lib/api";
import { Logo } from "@/components/brand";

const GROUPS: [string, [string, string][]][] = [
  ["Monitor", [["/", "Overview"], ["/jobs", "Jobs"], ["/failures", "Failures"], ["/incidents", "Incidents"], ["/logs", "Logs"]]],
  ["Platform", [["/kubernetes", "Kubernetes"], ["/topology", "Topology"], ["/analytics", "Analytics"], ["/agents", "Servers & agents"]]],
  ["Workspace", [["/copilot", "AI Copilot"], ["/alerting", "Alerting"], ["/integrations", "Integrations"], ["/billing", "Billing"], ["/settings", "Settings"]]],
];

function NavList({ bad, onNav }: { bad: number; onNav?: () => void }) {
  const path = usePathname();
  return (
    <nav className="flex-1 space-y-5 overflow-auto px-3 py-2">
      {GROUPS.map(([group, items]) => (
        <div key={group}>
          <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-mute">{group}</p>
          {items.map(([href, label]) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} onClick={onNav} className={clsx("mb-0.5 flex items-center justify-between rounded-xl px-3 py-2 text-sm", active ? "bg-accent text-white font-medium" : "text-ink/80 hover:bg-accent/5")}>
                {label}{href === "/failures" && bad > 0 && <span className={clsx("rounded-full px-1.5 text-[11px] font-medium", active ? "bg-white/20 text-white" : "bg-bad/10 text-bad")}>{bad}</span>}
              </Link>
            );
          })}
        </div>
      ))}
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
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-panel px-4 py-3 lg:hidden">
        <Logo />
        <button aria-label="Menu" onClick={() => setOpen(true)} className="btn px-3">Menu{bad > 0 && <span className="ml-1 rounded-full bg-bad/10 px-1.5 text-[11px] text-bad">{bad}</span>}</button>
      </header>
      {open && <div className="fixed inset-0 z-40 bg-ink/40 lg:hidden" onClick={() => setOpen(false)}>
        <aside className="flex h-full w-72 flex-col bg-panel py-4" onClick={(e) => e.stopPropagation()}>
          <div className="px-4 pb-3"><Logo /></div>
          <NavList bad={bad} onNav={() => setOpen(false)} />
          <button className="px-6 py-3 text-left text-sm text-mute" onClick={signOut}>Sign out</button>
        </aside>
      </div>}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-line bg-panel lg:flex">
        <div className="px-4 py-5"><Logo /></div>
        <NavList bad={bad} />
        <div className="space-y-2 border-t border-line px-5 py-4 text-xs text-mute">
          <div><span className="kbd">⌘K</span> search</div>
          <button className="font-medium text-accent hover:underline" onClick={signOut}>Sign out</button>
        </div>
      </aside>
    </>
  );
}
