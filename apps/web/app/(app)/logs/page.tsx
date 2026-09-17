"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, type Job } from "@/lib/api";
import { ts } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox, Empty } from "@/components/ui";

interface LogRow { execution_id: string; stream: string; content: string; job_id: string; job_name: string; status: string; host: string | null; scheduled_ts: string; exit_code: number | null }

function LogsInner() {
  const sp = useSearchParams();
  const [f, setF] = useState({ q: sp.get("q") ?? "", job_id: sp.get("job_id") ?? "", stream: sp.get("stream") ?? "", host: "", status: "", hours: "24" });
  const [applied, setApplied] = useState(f);
  const jobs = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?limit=200") });
  const params = new URLSearchParams(Object.entries({ q: applied.q, job_id: applied.job_id, stream: applied.stream, host: applied.host, status: applied.status, since: new Date(Date.now() - Number(applied.hours) * 3600e3).toISOString(), limit: "200" }).filter(([, v]) => v));
  const q = useQuery({ queryKey: ["logs", params.toString()], queryFn: () => api<{ items: LogRow[]; hosts: string[] }>(`/api/v1/logs/search?${params}`) });
  const sel = "rounded-md border border-line bg-panel px-2 py-1.5 text-sm";
  const hl = (c: string) => applied.q ? c.split(new RegExp(`(${applied.q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig")).map((p, i) => p.toLowerCase() === applied.q.toLowerCase() ? <mark key={i} className="bg-warn/30">{p}</mark> : p) : c;
  return (
    <Page title="Logs">
      <form className="mb-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setApplied(f); }}>
        <input className={`${sel} min-w-[260px] flex-1 font-mono`} placeholder="Search stdout/stderr…  e.g. timeout, OOM, exit" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
        <select className={sel} value={f.job_id} onChange={(e) => setF({ ...f, job_id: e.target.value })}><option value="">All jobs</option>{jobs.data?.items.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}</select>
        <select className={sel} value={f.stream} onChange={(e) => setF({ ...f, stream: e.target.value })}><option value="">stdout + stderr</option><option value="stderr">stderr only</option><option value="stdout">stdout only</option></select>
        <select className={sel} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">Any outcome</option><option value="failed">Failed</option><option value="timeout">Timed out</option><option value="success">Success</option></select>
        <select className={sel} value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })}><option value="">All hosts</option>{q.data?.hosts.map((h) => <option key={h}>{h}</option>)}</select>
        <select className={sel} value={f.hours} onChange={(e) => setF({ ...f, hours: e.target.value })}><option value="1">Last hour</option><option value="24">Last 24h</option><option value="168">Last 7 days</option><option value="720">Last 30 days</option></select>
        <button className="btn btn-primary" type="submit">Search</button>
      </form>
      {q.error ? <ErrorBox error={q.error} /> : q.isLoading ? <Skeleton /> : q.data!.items.length === 0
        ? <Empty title="No log lines match" hint="Logs come from cs-run wrappers and JSON heartbeats with stdout_tail / stderr_tail. Widen the time range or clear filters." />
        : <div className="space-y-2">{q.data!.items.map((r, k) => (
            <details key={k} open={!!applied.q || r.stream === "stderr"} className="rounded-lg border border-line bg-panel">
              <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm">
                <span className="font-mono text-xs text-mute">{ts(r.scheduled_ts)}</span><Link href={`/jobs/${r.job_id}`} className="font-medium hover:underline">{r.job_name}</Link>
                <Status s={r.status} /><span className={`font-mono text-xs ${r.stream === "stderr" ? "text-bad" : "text-mute"}`}>{r.stream}</span>{r.host && <span className="font-mono text-xs text-mute">{r.host}</span>}
                <span className="ml-auto font-mono text-xs text-mute">exit {r.exit_code ?? "—"}</span></summary>
              <pre className={`max-h-72 overflow-auto whitespace-pre-wrap border-t border-line px-3 py-2 font-mono text-xs ${r.stream === "stderr" ? "text-bad" : ""}`}>{hl(r.content)}</pre>
            </details>))}</div>}
    </Page>
  );
}
export default function LogsPage() { return <Suspense><LogsInner /></Suspense>; }
