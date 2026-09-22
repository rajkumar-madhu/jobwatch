"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, type Job, type Overview, type Incident } from "@/lib/api";
import { ago, dur } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox, Empty } from "@/components/ui";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

export default function OverviewPage() {
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/v1/analytics/overview") });
  const attention = useQuery({ queryKey: ["jobs", "attention"], queryFn: async () => {
    const all = await api<{ items: Job[] }>("/api/v1/jobs?limit=200");
    return all.items.filter((j) => ["failed", "missed", "late", "timeout"].includes(j.status)).sort((a, b) => (b.last_run_at ?? "").localeCompare(a.last_run_at ?? ""));
  }});
  const running = useQuery({ queryKey: ["jobs", "running"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?status=running&limit=50") });
  const inc = useQuery({ queryKey: ["incidents", "open"], queryFn: () => api<Incident[]>("/api/v1/incidents?status=open&limit=5") });
  const series = useQuery({ queryKey: ["series", 7], queryFn: () => api<any>("/api/v1/analytics/series?days=7") });
  const pts = (series.data?.points ?? []).map((p: any) => ({ t: new Date(p.t).toLocaleDateString(undefined, { weekday: "short" }), ok: p.ok, failed: p.failed, missed: p.missed }));

  if (ov.error) return <Page title="Overview"><ErrorBox error={ov.error} /></Page>;
  const d = ov.data;
  const bad = d ? (d.by_status.failed ?? 0) + (d.by_status.missed ?? 0) + (d.by_status.timeout ?? 0) : 0;
  const late = d?.by_status.late ?? 0;

  return (
    <Page title="Overview">
      {/* One sentence that answers "is anything wrong right now?" */}
      <p className="mb-6 text-base">
        {!d ? <span className="skeleton inline-block h-5 w-72" /> : bad + late === 0
          ? <>All <b>{d.total_jobs}</b> jobs are healthy. {d.executions_today} runs today, {d.success_rate_today ?? 100}% succeeded.</>
          : <><b className="text-bad">{bad} job{bad === 1 ? "" : "s"} need attention</b>{late > 0 && <>, {late} running late</>}. {d.executions_today} runs today, {d.success_rate_today}% succeeded.</>}
      </p>

      {d && <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {([["healthy", "Healthy"], ["running", "Running"], ["late", "Late"], ["missed", "Missed"], ["failed", "Failed"]] as [string, string][]).map(([k, l]) => (
          <div key={k} className="card px-4 py-3"><div className="text-2xl font-semibold tabular-nums text-accent">{d.by_status[k] ?? 0}</div><div className="mt-1 flex items-center gap-1 text-xs text-mute"><span className={`dot dot-${k}`} />{l}</div></div>))}
      </div>}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[3fr_2fr]">
        <section>
          <h2 className="mb-2 text-sm font-medium">Needs attention</h2>
          {attention.isLoading ? <Skeleton rows={4} /> : attention.data?.length === 0
            ? <Empty title="Nothing failed, missed or late" hint="Jobs that break will show up here first." />
            : <div className="tbl rounded-lg border border-line">
                {attention.data?.map((j) => (
                  <Link key={j.id} href={`/jobs/${j.id}`} className="row grid-cols-[1fr_120px_130px]">
                    <div className="truncate font-medium">{j.name}<span className="ml-2 font-normal text-mute">{j.schedule_human}</span></div>
                    <Status s={j.status} />
                    <span className="text-mute">{ago(j.last_run_at)}</span>
                  </Link>
                ))}
              </div>}

          <h2 className="mb-2 mt-8 text-sm font-medium">Last 7 days</h2>
          <div className="card h-44 p-3">{pts.length > 0 ? <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}><BarChart data={pts}><XAxis dataKey="t" tick={{ fontSize: 11 }} /><Tooltip /><Bar dataKey="ok" stackId="a" fill="rgb(var(--ok))" name="succeeded" /><Bar dataKey="failed" stackId="a" fill="rgb(var(--bad))" name="failed" /><Bar dataKey="missed" stackId="a" fill="rgb(var(--warn))" name="missed" /></BarChart></ResponsiveContainer> : <div className="skeleton h-full" />}</div>
          <h2 className="mb-2 mt-8 text-sm font-medium">Running now</h2>
          {running.data?.items.length === 0 ? <p className="text-sm text-mute">Nothing is executing at the moment.</p>
            : <div className="tbl rounded-lg border border-line">{running.data?.items.map((j) => (
                <Link key={j.id} href={`/jobs/${j.id}`} className="row grid-cols-[1fr_auto]"><span className="font-medium">{j.name}</span><span className="text-mute">started {ago(j.last_run_at)}</span></Link>))}</div>}
        </section>

        <section className="space-y-8">
          <div>
            <h2 className="mb-2 text-sm font-medium">Open incidents</h2>
            {inc.data?.length === 0 ? <p className="text-sm text-mute">No open incidents.</p>
              : <div className="tbl rounded-lg border border-line">{inc.data?.map((i) => (
                  <Link key={i.id} href={`/incidents/${i.id}`} className="row grid-cols-[1fr_auto]"><span className="truncate">{i.title}</span><span className="text-xs text-mute">{ago(i.started_at)}</span></Link>))}</div>}
          </div>
          <div>
            <h2 className="mb-2 text-sm font-medium">Slowest jobs, 7 days (p95)</h2>
            <ul className="tbl rounded-lg border border-line">{(d?.top_slowest_7d ?? []).map((r) => (
              <li key={r.name} className="row grid-cols-[1fr_auto]"><span className="truncate">{r.name}</span><span className="font-mono text-xs">{dur(r.p95_ms ?? null)}</span></li>))}
              {(d?.top_slowest_7d?.length ?? 0) === 0 && <li className="px-4 py-3 text-sm text-mute">No completed runs yet.</li>}</ul>
          </div>
          <div>
            <h2 className="mb-2 text-sm font-medium">Most failures, 7 days</h2>
            <ul className="tbl rounded-lg border border-line">{(d?.top_failing_7d ?? []).map((r) => (
              <li key={r.name} className="row grid-cols-[1fr_auto]"><span className="truncate">{r.name}</span><span className="text-bad">{r.failures}</span></li>))}
              {(d?.top_failing_7d?.length ?? 0) === 0 && <li className="px-4 py-3 text-sm text-mute">No failures this week.</li>}</ul>
          </div>
        </section>
      </div>
    </Page>
  );
}
