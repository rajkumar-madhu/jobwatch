"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { Page, Status, Skeleton, ErrorBox, Empty } from "@/components/ui";

function Node({ label, status, children, defaultOpen, mono }: { label: string; status: string; children?: React.ReactNode; defaultOpen?: boolean; mono?: boolean }) {
  const [open, setOpen] = useState(defaultOpen ?? status !== "healthy");
  return (
    <li className="ml-4 border-l border-line pl-3">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 py-1 text-left text-sm hover:text-accent">
        <span className="w-3 text-mute">{children ? (open ? "▾" : "▸") : ""}</span><span className={`dot dot-${status}`} /><span className={mono ? "font-mono text-xs" : ""}>{label}</span></button>
      {open && children && <ul>{children}</ul>}
    </li>
  );
}
const Leaf = ({ j }: { j: any }) => <li className="ml-7 border-l border-line pl-3"><Link href={`/jobs/${j.id}`} className="flex items-center gap-2 py-1 text-sm hover:underline"><span className={`dot dot-${j.status}`} />{j.name}</Link></li>;

export default function TopologyPage() {
  const q = useQuery({ queryKey: ["topology"], queryFn: () => api<any>("/api/v1/topology") });
  const dep = useQuery({ queryKey: ["deps"], queryFn: () => api<{ nodes: any[]; edges: any[] }>("/api/v1/dependencies") });
  if (q.error) return <Page title="Topology"><ErrorBox error={q.error} /></Page>;
  if (!q.data) return <Page title="Topology"><Skeleton /></Page>;
  const d = q.data;
  // simple layered DAG layout: depth = longest path from roots
  const nodes = dep.data?.nodes ?? [], edges = dep.data?.edges ?? [];
  const depth: Record<string, number> = {}; const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const calc = (id: string, seen = new Set<string>()): number => { if (depth[id] != null) return depth[id]; if (seen.has(id)) return 0; seen.add(id); const ups = edges.filter((e) => e.job_id === id).map((e) => calc(e.depends_on_job_id, seen)); return (depth[id] = ups.length ? Math.max(...ups) + 1 : 0); };
  nodes.forEach((n) => calc(n.id));
  const layers: any[][] = []; nodes.forEach((n) => (layers[depth[n.id]] ||= []).push(n));
  return (
    <Page title="Topology">
      <p className="mb-4 text-sm">Overall: <Status s={d.status} /> — folders open automatically where something is wrong.</p>
      <div className="grid gap-8 lg:grid-cols-2">
        <section><h2 className="mb-2 text-sm font-medium">Organisation → Servers → Users → Jobs</h2>
          {d.servers.length === 0 ? <Empty title="No servers" hint="Install the Linux agent to populate this tree." /> : <ul>{d.servers.map((s: any) => (
            <Node key={s.id} label={s.name} status={s.status} mono>{s.users.map((u: any) => <Node key={u.name} label={u.name} status={u.status} mono>{u.jobs.map((j: any) => <Leaf key={j.id} j={j} />)}</Node>)}</Node>))}</ul>}
          {d.heartbeat_only.length > 0 && <ul className="mt-2"><Node label="Heartbeat-only jobs" status={d.heartbeat_only.some((j: any) => ["failed", "missed"].includes(j.status)) ? "failed" : "healthy"}>{d.heartbeat_only.map((j: any) => <Leaf key={j.id} j={j} />)}</Node></ul>}
        </section>
        <section><h2 className="mb-2 text-sm font-medium">Organisation → Clusters → Namespaces → CronJobs</h2>
          {d.clusters.length === 0 ? <Empty title="No clusters" hint="Install the Helm chart to populate this tree." /> : <ul>{d.clusters.map((c: any) => (
            <Node key={c.id} label={c.name} status={c.status} mono>{c.namespaces.map((n: any) => <Node key={n.name} label={n.name} status={n.status} mono>{n.cronjobs.map((j: any) => <Leaf key={j.id} j={j} />)}</Node>)}</Node>))}</ul>}
        </section>
      </div>
      <section className="mt-10"><h2 className="mb-2 text-sm font-medium">Dependencies</h2>
        {nodes.length === 0 ? <p className="text-sm text-mute">No dependencies declared. Add them from a job page (“depends on”) to see downstream impact when something fails.</p>
          : <div className="overflow-x-auto rounded-lg border border-line p-4"><div className="flex gap-10">{layers.map((layer, i) => (
              <div key={i} className="flex flex-col gap-3">{layer.map((n) => { const ups = edges.filter((e) => e.job_id === n.id).map((e) => byId[e.depends_on_job_id]?.name).filter(Boolean); return (
                <Link key={n.id} href={`/jobs/${n.id}`} className={`rounded-md border px-3 py-2 text-sm ${["failed", "missed", "timeout"].includes(n.status) ? "border-bad bg-bad/5" : "border-line bg-panel"}`}>
                  <div className="flex items-center gap-2 font-medium"><span className={`dot dot-${n.status}`} />{n.name}</div>{ups.length > 0 && <div className="text-xs text-mute">after {ups.join(", ")}</div>}</Link>); })}</div>))}</div>
              <p className="mt-2 text-xs text-mute">Left to right = execution order. Red = failing; everything to its right is at risk.</p></div>}
      </section>
    </Page>
  );
}
