"use client";
import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Job } from "@/lib/api";

const PAGES: [string, string][] = [
  ["Overview", "/"],
  ["Jobs", "/jobs"],
  ["Failures", "/failures"],
  ["Incidents", "/incidents"],
  ["Logs", "/logs"],
  ["Kubernetes", "/kubernetes"],
  ["Topology", "/topology"],
  ["Analytics", "/analytics"],
  ["Servers & agents", "/agents"],
  ["AI Copilot", "/copilot"],
  ["Alerting", "/alerting"],
  ["Integrations", "/integrations"],
  ["Billing", "/billing"],
  ["Settings", "/settings"],
];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { data } = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?limit=200"), enabled: open });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
      if (!open && !e.metaKey && !e.ctrlKey && (e.target as HTMLElement).tagName !== "INPUT" && (e.target as HTMLElement).tagName !== "TEXTAREA") {
        const w = window as Window & { __g?: number };
        if (e.key === "g") w.__g = Date.now();
        else if (w.__g && Date.now() - w.__g < 800) {
          const map: Record<string, string> = { o: "/", j: "/jobs", f: "/failures", i: "/incidents", l: "/logs", a: "/agents", k: "/kubernetes", s: "/settings" };
          if (map[e.key]) router.push(map[e.key]);
          w.__g = 0;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, router]);

  const go = (p: string) => { setOpen(false); router.push(p); };
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-ink/30 p-4 pt-[12vh]" onClick={() => setOpen(false)}>
      <Command className="mx-auto w-full max-w-lg overflow-hidden rounded-lg border border-line bg-panel shadow-xl" onClick={(e) => e.stopPropagation()} label="Command palette">
        <Command.Input autoFocus placeholder="Jump to a job, page, or action…" className="w-full border-b border-line bg-transparent px-4 py-3 outline-none" />
        <Command.List className="max-h-80 overflow-auto p-2">
          <Command.Empty className="px-2 py-4 text-sm text-mute">Nothing matches.</Command.Empty>
          <Command.Group heading="Pages" className="text-xs text-mute [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
            {PAGES.map(([l, p]) => (
              <Command.Item key={p} onSelect={() => go(p)} className="cursor-pointer rounded px-2 py-1.5 text-sm text-ink data-[selected=true]:bg-accent/10">{l}</Command.Item>
            ))}
          </Command.Group>
          <Command.Group heading="Jobs" className="text-xs text-mute [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
            {data?.items.map((j) => (
              <Command.Item key={j.id} value={`${j.name} ${(j.tags ?? []).join(" ")}`} onSelect={() => go(`/jobs/${j.id}`)} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-ink data-[selected=true]:bg-accent/10">
                <span className={`dot dot-${j.status}`} />{j.name}<span className="ml-auto text-xs text-mute">{j.schedule_human ?? "heartbeat"}</span>
              </Command.Item>
            ))}
          </Command.Group>
          <Command.Group heading="Actions" className="text-xs text-mute [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
            <Command.Item onSelect={() => go("/jobs?new=1")} className="cursor-pointer rounded px-2 py-1.5 text-sm text-ink data-[selected=true]:bg-accent/10">Add a job</Command.Item>
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}
