"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api, mutate, type Job, type Execution } from "@/lib/api";
import { ingestOrigin } from "@/lib/urls";
import { ago, dur, ts, statusLabel } from "@/lib/format";
import { Page, Status, Strip, Skeleton, ErrorBox, Empty } from "@/components/ui";

interface ExecDetail { execution: Execution & Record<string, any>; timeline: { sequence: number; kind: string; agent_ts: string | null; server_ts: string; payload: any }[]; logs: { stdout: string; stderr: string } }

function Timeline({ id, onCompare, job }: { id: string; onCompare: (id: string) => void; job?: Job }) {
  const q = useQuery({ queryKey: ["exec", id], queryFn: () => api<ExecDetail>(`/api/v1/executions/${id}`) });
  if (q.isLoading) return <Skeleton rows={4} />;
  if (q.error) return <ErrorBox error={q.error} />;
  const { execution: e, timeline, logs } = q.data!;
  const t0 = new Date(timeline[0]?.agent_ts ?? timeline[0]?.server_ts ?? e.scheduled_ts).getTime();
  return (
    <div className="rounded-lg border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-line px-4 py-3 text-sm">
        <Status s={e.status} /><span>Took <b>{dur(e.duration_ms)}</b>{e.duration_ms && job?.expected_runtime_s && e.duration_ms > job.expected_runtime_s * 1000 && <span className="ml-1 text-warn">— over expected {dur(job.expected_runtime_s * 1000)}</span>}</span>
        <span>Exit <span className="font-mono">{e.exit_code ?? "—"}</span></span>
        {e.host && <span>on <span className="font-mono">{e.host}</span></span>}
        {e.skew_ms !== 0 && <span className="text-mute">clock skew {e.skew_ms} ms</span>}
        <button className="btn ml-auto" onClick={() => onCompare(id)}>Compare with previous</button>
      </div>
      <ol className="px-4 py-3">
        {timeline.map((t) => {
          const at = new Date(t.agent_ts ?? t.server_ts).getTime();
          return (
            <li key={t.sequence} className="grid grid-cols-[90px_80px_1fr] gap-3 py-1 text-sm">
              <span className="font-mono text-xs text-mute">{ts(t.agent_ts ?? t.server_ts)}</span>
              <span className="font-mono text-xs text-mute">+{dur(at - t0)}</span>
              <span>{({ scheduled: "Scheduled", start: "Started", started: "Started", progress: "Still running", success: "Completed", fail: "Failed" } as any)[t.kind] ?? t.kind}
                {t.payload?.command && <span className="ml-2 font-mono text-xs text-mute">{t.payload.command}</span>}
                {t.payload?.passive && <span className="ml-2 text-xs text-warn">passive · exit code unknown</span>}</span>
            </li>);
        })}
      </ol>
      {(logs.stderr || logs.stdout) && (
        <div className={`grid gap-px border-t border-line bg-line ${logs.stderr && logs.stdout ? "sm:grid-cols-2" : ""}`}>
          {(["stderr", "stdout"] as const).map((s) => logs[s] && (
            <div key={s} className="bg-panel p-3"><div className="mb-1 text-xs text-mute">{s} (tail)</div>
              <pre className={`max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs ${s === "stderr" ? "text-bad" : ""}`}>{logs[s]}</pre></div>))}
        </div>)}
    </div>
  );
}

function Compare({ a, b, onClose }: { a: string; b: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["compare", a, b], queryFn: () => api<{ a: any; b: any; delta: { duration_ms: number; pct: number } | null }>(`/api/v1/executions/${a}/compare/${b}`) });
  if (!q.data) return null;
  const rows: [string, (x: any) => string][] = [["Status", (x) => statusLabel[x.status] ?? x.status], ["Duration", (x) => dur(x.duration_ms)], ["Exit code", (x) => String(x.exit_code ?? "—")], ["Host", (x) => x.host ?? "—"], ["Started", (x) => ts(x.agent_ts_start)]];
  return (
    <div className="mb-4 rounded-lg border border-line bg-panel p-4">
      <div className="mb-2 flex items-center justify-between text-sm"><b>Comparison</b>{q.data.delta && <span className={q.data.delta.pct > 20 ? "text-warn" : "text-mute"}>{q.data.delta.pct > 0 ? "+" : ""}{q.data.delta.pct}% duration vs previous</span>}<button className="btn" onClick={onClose}>Close</button></div>
      <table className="w-full text-sm"><tbody>{rows.map(([l, f]) => (
        <tr key={l} className="border-t border-line"><td className="py-1.5 text-mute">{l}</td><td className="font-mono text-xs">{f(q.data!.b)}</td><td className="font-mono text-xs">{f(q.data!.a)}</td></tr>))}</tbody>
        <thead><tr className="text-xs text-mute"><th /><th className="text-left font-normal">previous</th><th className="text-left font-normal">selected</th></tr></thead></table>
    </div>
  );
}

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const job = useQuery({ queryKey: ["job", id], queryFn: () => api<Job>(`/api/v1/jobs/${id}`) });
  const execs = useQuery({ queryKey: ["execs", id, 100], queryFn: () => api<Execution[]>(`/api/v1/jobs/${id}/executions?limit=100`) });
  const next = useQuery({ queryKey: ["next", id], queryFn: () => api<{ human: string | null; runs: string[] }>(`/api/v1/jobs/${id}/next-runs?n=3`) });
  const [sel, setSel] = useState<string | null>(null);
  const [cmp, setCmp] = useState<[string, string] | null>(null);
  const impact = useQuery({ queryKey: ["impact", id], queryFn: () => api<{ upstream: any[]; downstream: any[] }>(`/api/v1/jobs/${id}/impact`) });
  const allJobs = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?limit=200") });
  const [depSel, setDepSel] = useState("");
  const addDep = useMutation({ mutationFn: () => mutate("/api/v1/dependencies", "post", { body: { job_id: id, depends_on_job_id: depSel } }), onSuccess: () => { setDepSel(""); qc.invalidateQueries({ queryKey: ["impact", id] }); } });
  const rmDep = useMutation({ mutationFn: (d: string) => mutate("/api/v1/dependencies", "delete", { query: { job_id: id, depends_on_job_id: d } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["impact", id] }) });
  const pause = useMutation({ mutationFn: (paused: boolean) => mutate("/api/v1/jobs/{job_id}", "patch", { path: { job_id: id }, body: { paused } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["job", id] }) });
  if (job.error) return <Page title="Job"><ErrorBox error={job.error} /></Page>;
  if (!job.data) return <Page title="Job"><Skeleton /></Page>;
  const j = job.data; const list = execs.data ?? []; const selected = sel ?? list[0]?.id;
  const compare = (eid: string) => { const i = list.findIndex((e) => e.id === eid); if (i >= 0 && list[i + 1]) setCmp([list[i + 1].id, eid]); };
  const rate = list.length ? Math.round(100 * list.filter((e) => e.status === "success").length / list.filter((e) => e.status !== "running" && e.status !== "scheduled").length || 0) : null;

  return (
    <Page title={<span className="flex items-center gap-3">{j.name}<Status s={j.status} /></span>}
          actions={<div className="flex gap-2"><Link href={`/copilot?job_id=${id}&q=${encodeURIComponent(`Why is ${j.name} ${j.status}?`)}`} className="btn">Ask Copilot</Link><button className="btn" onClick={() => pause.mutate(!j.paused)}>{j.paused ? "Resume monitoring" : "Pause monitoring"}</button></div>}>
      <dl className="mb-5 grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-5">
        <div><dt className="text-mute">Schedule</dt><dd>{j.schedule_human ?? "Heartbeat only"}{j.schedule_expr && <span className="ml-2 font-mono text-xs text-mute">{j.schedule_expr} · {j.tz}</span>}</dd></div>
        <div><dt className="text-mute">Next expected</dt><dd>{next.data?.runs[0] ? `${ts(next.data.runs[0])} (${ago(next.data.runs[0])})` : "—"}</dd></div>
        <div><dt className="text-mute">Last run</dt><dd>{j.last_run_at ? `${ago(j.last_run_at)} · ${statusLabel[j.last_status ?? ""] ?? j.last_status}` : "Never"}</dd></div>
        <div><dt className="text-mute">Success rate (last {list.length})</dt><dd>{rate == null ? "—" : `${rate}%`}</dd></div>
        <div><dt className="text-mute">Expected runtime</dt><dd>{j.expected_runtime_s ? dur(j.expected_runtime_s * 1000) : "not set"} · grace {j.grace_s}s</dd></div>
      </dl>

      <div className="mb-5">
        <div className="mb-1 flex items-baseline justify-between text-xs text-mute"><span>Run history — bar height = duration, click to inspect</span><span>older → newer</span></div>
        {execs.isLoading ? <div className="skeleton h-6" /> : list.length === 0 ? <Empty title="No runs recorded yet" hint={`Ping this job: curl ${ingestOrigin()}/ping/${j.heartbeat_token}`} />
          : <div className="rounded-md border border-line px-3 py-2"><Strip execs={list} onPick={setSel} /></div>}
      </div>

      {cmp && <Compare a={cmp[0]} b={cmp[1]} onClose={() => setCmp(null)} />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div>{selected ? <Timeline id={selected} onCompare={compare} job={j} /> : <p className="text-sm text-mute">Select a run.</p>}</div>
        <div className="tbl rounded-lg border border-line">
          <div className="row grid-cols-[110px_1fr_70px_50px] th"><span>Started</span><span>Status</span><span className="text-right">Took</span><span className="text-right">Exit</span></div>
          <div className="max-h-[520px] overflow-auto">
            {list.map((e) => (
              <button key={e.id} onClick={() => setSel(e.id)} className={`row w-full grid-cols-[110px_1fr_70px_50px] text-left ${selected === e.id ? "bg-accent/5" : ""}`}>
                <span className="font-mono text-xs">{ts(e.agent_ts_start ?? e.scheduled_ts)}</span><Status s={e.status} />
                <span className="text-right font-mono text-xs">{dur(e.duration_ms)}</span><span className="text-right font-mono text-xs">{e.exit_code ?? "—"}</span>
              </button>))}
          </div>
        </div>
      </div>

      <section className="mt-6 grid gap-4 text-sm sm:grid-cols-2">
        <div className="rounded-lg border border-line p-3"><div className="mb-1 font-medium">Depends on</div>
          {impact.data?.upstream.length ? impact.data.upstream.map((u) => <div key={u.id} className="flex items-center justify-between py-1"><Link href={`/jobs/${u.id}`} className="flex items-center gap-2 hover:underline"><span className={`dot dot-${u.status}`} />{u.name}</Link><button className="text-xs text-mute hover:text-bad" onClick={() => rmDep.mutate(u.id)}>remove</button></div>) : <p className="text-mute">Runs independently.</p>}
          <div className="mt-2 flex gap-2"><select className="flex-1 rounded-md border border-line bg-panel px-2 py-1" value={depSel} onChange={(e) => setDepSel(e.target.value)}><option value="">Add upstream job…</option>{allJobs.data?.items.filter((x) => x.id !== id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select><button className="btn" disabled={!depSel} onClick={() => addDep.mutate()}>Add</button></div>
          {addDep.error && <p className="mt-1 text-xs text-bad">{(addDep.error as Error).message}</p>}</div>
        <div className="rounded-lg border border-line p-3"><div className="mb-1 font-medium">Downstream impact if this fails</div>
          {impact.data?.downstream.length ? impact.data.downstream.map((u) => <Link key={u.id} href={`/jobs/${u.id}`} className="flex items-center gap-2 py-1 hover:underline"><span className={`dot dot-${u.status}`} /><span className="text-mute">{"→".repeat(u.depth)}</span>{u.name}</Link>) : <p className="text-mute">Nothing depends on this job.</p>}</div>
      </section>
      <details className="mt-6 text-sm"><summary className="cursor-pointer text-mute">Integration snippets</summary>
        <pre className="mt-2 overflow-auto rounded-md border border-line bg-panel p-3 font-mono text-xs">{`# simple
curl -fsS ${ingestOrigin()}/ping/${j.heartbeat_token}

# start/finish with exit code (bash)
H=${ingestOrigin()}/heartbeat/${j.heartbeat_token}
curl -fsS $H/start; ./your-job.sh && curl -fsS $H/success || curl -fsS $H/fail

# agent wrapper (captures logs, duration, exit code)
cs-run --job ${j.heartbeat_token} -- ./your-job.sh`}</pre></details>
    </Page>
  );
}
