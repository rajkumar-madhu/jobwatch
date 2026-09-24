"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { ago, ts } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox, Empty } from "@/components/ui";

export default function KubernetesPage() {
  const cl = useQuery({ queryKey: ["clusters"], queryFn: () => api<any[]>("/api/v1/clusters") });
  const [sel, setSel] = useState<string | null>(null); const [ns, setNs] = useState("");
  const cid = sel ?? cl.data?.[0]?.id;
  const cj = useQuery({ queryKey: ["cronjobs", cid, ns], queryFn: () => api<any>(`/api/v1/clusters/${cid}/cronjobs${ns ? `?namespace=${ns}` : ""}`), enabled: !!cid });
  const namespaces = Array.from(new Set((cj.data?.cronjobs ?? []).map((c: any) => c.namespace))) as string[];
  if (cl.error) return <Page title="Kubernetes"><ErrorBox error={cl.error} /></Page>;
  return (
    <Page title="Kubernetes">
      {cl.isLoading ? <Skeleton /> : cl.data!.length === 0 ? <Empty title="No clusters connected" hint="Install the Helm chart (Servers & agents → Install an agent, choose Kubernetes). It watches CronJobs, Jobs, Pods and Events read-only." />
        : <>
          <div className="mb-4 flex flex-wrap gap-2">{cl.data!.map((c) => (
            <button key={c.id} onClick={() => { setSel(c.id); setNs(""); }} className={`rounded-lg border px-3 py-2 text-left text-sm ${cid === c.id ? "border-accent bg-accent/5" : "border-line"}`}>
              <div className="flex items-center gap-2 font-medium"><span className={`dot ${c.failing > 0 ? "dot-failed" : "dot-healthy"}`} />{c.name}</div>
              <div className="text-xs text-mute">{c.cronjobs} CronJobs · {c.failing} failing · agent {ago(c.last_seen_at)} · {c.scope === "cluster" ? "cluster-wide" : `${c.namespaces?.length} namespaces`}</div></button>))}</div>
          <div className="mb-3 flex flex-wrap gap-1" role="toolbar" aria-label="Filter by namespace">
            <button type="button" aria-pressed={!ns} onClick={() => setNs("")} className={`rounded-md px-2.5 py-1 text-sm ${!ns ? "bg-ink/10 font-medium" : "text-mute"}`}>All namespaces</button>
            {namespaces.map((n) => (
              <button key={n} type="button" aria-pressed={ns === n} onClick={() => setNs(n)} className={`rounded-md px-2.5 py-1 text-sm font-mono ${ns === n ? "bg-ink/10 font-medium" : "text-mute"}`}>{n}</button>
            ))}
          </div>
          {cj.isLoading ? <Skeleton /> : <div className="tbl rounded-lg border border-line">
            <div className="row grid-cols-[minmax(0,2fr)_110px_120px_80px_110px_110px_minmax(0,1.5fr)] th"><span>CronJob</span><span>Status</span><span>Schedule</span><span>Conc.</span><span>Last schedule</span><span>Last success</span><span>Last failure reason</span></div>
            {(cj.data?.cronjobs ?? []).map((c: any) => (
              <Link key={`${c.namespace}/${c.name}`} href={c.job_id ? `/jobs/${c.job_id}` : "#"} className="row grid-cols-[minmax(0,2fr)_110px_120px_80px_110px_110px_minmax(0,1.5fr)]">
                <div className="min-w-0"><div className="truncate font-medium"><span className="text-mute">{c.namespace}/</span>{c.name}{c.suspend && <span className="ml-2 rounded bg-mute/15 px-1 text-[10px] text-mute">suspended</span>}</div><div className="truncate font-mono text-xs text-mute">{c.image}</div></div>
                <Status s={c.status ?? "unknown"} /><span className="font-mono text-xs">{c.schedule}</span><span className="text-xs text-mute">{c.concurrency_policy || "Allow"}</span>
                <span className="text-xs text-mute">{ago(c.last_schedule_at)}</span><span className="text-xs text-mute">{ago(c.last_success_at)}</span>
                <span className={`truncate text-xs ${c.last_reason ? "text-bad" : "text-mute"}`}>{c.last_reason ?? (c.failures_7d ? `${c.failures_7d} failures / 7d` : "—")}</span>
              </Link>))}</div>}
          <h2 className="mb-2 mt-8 text-sm font-medium">Recent warning events</h2>
          {(cj.data?.events?.length ?? 0) === 0 ? <p className="text-sm text-mute">No warnings from the cluster.</p> : <div className="tbl rounded-lg border border-line">{(cj.data?.events ?? []).map((e: any, k: number) => (
            <div key={k} className="row grid-cols-[130px_120px_1fr]"><span className="font-mono text-xs text-mute">{ts(e.ts)}</span><span className="text-xs text-warn">{e.reason}</span><span className="truncate text-xs"><span className="text-mute">{e.namespace}/{e.object_name}</span> — {e.message}</span></div>))}</div>}
        </>}
    </Page>
  );
}
