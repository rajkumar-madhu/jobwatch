"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, type Job, type Execution } from "@/lib/api";
import { ago } from "@/lib/format";
import { Page, Status, Strip, Skeleton, ErrorBox, Empty } from "@/components/ui";

const FILTERS = ["all", "failed", "missed", "late", "running", "healthy", "paused"];

function JobRow({ j }: { j: Job }) {
  const ex = useQuery({ queryKey: ["execs", j.id, 30], queryFn: () => api<Execution[]>(`/api/v1/jobs/${j.id}/executions?limit=30`), staleTime: 30_000 });
  return (
    <Link href={`/jobs/${j.id}`} className="row grid-cols-[minmax(0,2fr)_110px_minmax(0,1.5fr)_110px_60px]">
      <div className="min-w-0">
        <div className="truncate font-medium">{j.name}</div>
        <div className="truncate text-xs text-mute">{j.schedule_human ?? "Heartbeat only"}{(j.tags?.length ?? 0) > 0 && ` · ${(j.tags ?? []).join(", ")}`}</div>
      </div>
      <Status s={j.status} />
      <div>{ex.data ? <Strip execs={ex.data} /> : <div className="skeleton h-5" />}</div>
      <span className="text-mute">{ago(j.last_run_at)}</span>
      <span className="text-right font-mono text-xs">{j.reliability_score ?? "—"}</span>
    </Link>
  );
}

function NewJob({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const ws = useQuery({ queryKey: ["workspaces"], queryFn: () => api<{ id: string; name: string }[]>("/api/v1/workspaces") });
  const [f, setF] = useState({ name: "", schedule_expr: "", tz: "UTC", grace_s: 300, expected_runtime_s: "" });
  const [preview, setPreview] = useState<{ human: string } | null>(null);
  const m = useMutation({
    mutationFn: () => api<Job>("/api/v1/jobs", { method: "POST", body: JSON.stringify({ ...f, workspace_id: ws.data?.[0].id, schedule_expr: f.schedule_expr || null, expected_runtime_s: f.expected_runtime_s ? Number(f.expected_runtime_s) : null }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["jobs"] }); onDone(); },
  });
  const check = async () => { if (!f.schedule_expr) return setPreview(null); try { setPreview(await api(`/api/v1/jobs/schedule/preview?expr=${encodeURIComponent(f.schedule_expr)}&tz=${f.tz}`, { method: "POST" })); } catch { setPreview({ human: "Invalid expression" }); } };
  return (
    <div className="mb-4 rounded-lg border border-line bg-panel p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">Name<input className="mt-1 w-full rounded-md border border-line bg-bg px-2 py-1.5" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="nightly-database-backup" /></label>
        <label className="text-sm">Schedule (cron, optional)<input className="mt-1 w-full rounded-md border border-line bg-bg px-2 py-1.5 font-mono" value={f.schedule_expr} onChange={(e) => setF({ ...f, schedule_expr: e.target.value })} onBlur={check} placeholder="0 2 * * *" />
          {preview && <span className="text-xs text-mute">{preview.human}</span>}</label>
        <label className="text-sm">Timezone<input className="mt-1 w-full rounded-md border border-line bg-bg px-2 py-1.5" value={f.tz} onChange={(e) => setF({ ...f, tz: e.target.value })} /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">Grace (s)<input type="number" className="mt-1 w-full rounded-md border border-line bg-bg px-2 py-1.5" value={f.grace_s} onChange={(e) => setF({ ...f, grace_s: Number(e.target.value) })} /></label>
          <label className="text-sm">Expected runtime (s)<input type="number" className="mt-1 w-full rounded-md border border-line bg-bg px-2 py-1.5" value={f.expected_runtime_s} onChange={(e) => setF({ ...f, expected_runtime_s: e.target.value })} /></label>
        </div>
      </div>
      {m.error && <div className="mt-2"><ErrorBox error={m.error} /></div>}
      <div className="mt-3 flex gap-2"><button className="btn btn-primary" disabled={!f.name || m.isPending} onClick={() => m.mutate()}>Create job</button><button className="btn" onClick={onDone}>Cancel</button></div>
    </div>
  );
}

function JobsInner() {
  const params = useSearchParams();
  const statusParam = params.get("status");
  const initialFilter = statusParam && FILTERS.includes(statusParam) ? statusParam : "all";
  const [filter, setFilter] = useState(initialFilter);
  const [creating, setCreating] = useState(params.get("new") === "1");
  const q = useQuery({ queryKey: ["jobs", filter], queryFn: () => api<{ items: Job[] }>(`/api/v1/jobs?limit=200${filter !== "all" ? `&status=${filter}` : ""}`) });
  return (
    <Page title="Jobs" actions={<button className="btn btn-primary" onClick={() => setCreating(true)}>Add job</button>}>
      {creating && <NewJob onDone={() => setCreating(false)} />}
      <div className="mb-3 flex flex-wrap gap-1" role="toolbar" aria-label="Filter jobs by status">
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)} className={`rounded-md px-2.5 py-1 text-sm capitalize ${filter === f ? "bg-ink/10 font-medium" : "text-mute hover:text-ink"}`}>{f}</button>
        ))}
      </div>
      {q.error ? <ErrorBox error={q.error} /> : q.isLoading ? <Skeleton /> : q.data!.items.length === 0
        ? <Empty title={filter === "all" ? "No jobs yet" : `No ${filter} jobs`} hint={filter === "all" ? "Add a job manually, or install the agent and it will discover your crontabs." : "Try another filter."} action={filter === "all" && <button className="btn btn-primary" onClick={() => setCreating(true)}>Add job</button>} />
        : <div className="tbl rounded-lg border border-line">
            <div className="row grid-cols-[minmax(0,2fr)_110px_minmax(0,1.5fr)_110px_60px] th"><span>Job</span><span>Status</span><span>Last 30 runs</span><span>Last run</span><span className="text-right">Score</span></div>
            {q.data!.items.map((j) => <JobRow key={j.id} j={j} />)}
          </div>}
    </Page>
  );
}

export default function JobsPage() { return <Suspense><JobsInner /></Suspense>; }
