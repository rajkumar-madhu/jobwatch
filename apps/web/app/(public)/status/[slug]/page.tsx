"use client";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { ago, ts } from "@/lib/format";

const OVERALL: Record<string, [string, string]> = { operational: ["All scheduled jobs are running normally", "bg-ok"], delayed: ["Some jobs are running late", "bg-warn"], degraded: ["Some scheduled jobs have failed", "bg-bad"] };
export default function StatusPage() {
  const { slug } = useParams<{ slug: string }>();
  const q = useQuery({ queryKey: ["status", slug], queryFn: () => api<any>(`/public/status/${slug}`), refetchInterval: 30_000, retry: false });
  if (q.isError) return <main className="mx-auto max-w-2xl px-6 py-20 text-center text-mute">This status page doesn't exist or isn't public.</main>;
  if (!q.data) return <main className="mx-auto max-w-2xl px-6 py-20"><div className="skeleton h-16" /></main>;
  const d = q.data; const [msg, cls] = OVERALL[d.overall];
  return (
    <main className="mx-auto max-w-2xl px-6 py-14">
      <h1 className="text-xl font-semibold">{d.title}</h1>
      <div className={`mt-4 rounded-lg px-4 py-3 text-white ${cls}`}>{msg}</div>
      {d.maintenance.length > 0 && <div className="mt-3 rounded-lg border border-line px-4 py-3 text-sm">Scheduled maintenance: {d.maintenance.map((m: any) => `${ts(m.starts_at)} – ${ts(m.ends_at)}`).join("; ")}</div>}
      <section className="mt-8 rounded-lg border border-line">{d.jobs.map((j: any) => (
        <div key={j.name} className="border-b border-line px-4 py-3 last:border-0">
          <div className="flex items-center justify-between text-sm"><span className="flex items-center gap-2 font-medium"><span className={`dot dot-${j.status}`} />{j.name}</span><span className="text-mute">{j.uptime_90d != null ? `${j.uptime_90d}% · 90 days` : "no data"}</span></div>
          <div className="strip mt-2 !h-3">{[...j.recent].reverse().map((s: string, i: number) => <i key={i} className={s} style={{ height: "100%" }} />)}</div>
          <div className="mt-1 text-xs text-mute">last run {ago(j.last_run_at)}</div>
        </div>))}{d.jobs.length === 0 && <p className="px-4 py-6 text-center text-sm text-mute">No jobs are published on this page yet.</p>}</section>
      <h2 className="mt-8 text-sm font-medium">Incidents, last 30 days</h2>
      {d.incidents.length === 0 ? <p className="mt-1 text-sm text-mute">None.</p> : <ul className="mt-1 rounded-lg border border-line">{d.incidents.map((i: any, k: number) => (
        <li key={k} className="border-b border-line px-4 py-2 text-sm last:border-0"><span className="font-medium">{i.title}</span><span className="ml-2 text-mute">{ts(i.started_at)}{i.resolved_at ? ` — resolved ${ago(i.resolved_at)}` : " — ongoing"}</span></li>))}</ul>}
      <p className="mt-10 text-xs text-mute">Powered by WeCrew JobWatch</p>
    </main>
  );
}
