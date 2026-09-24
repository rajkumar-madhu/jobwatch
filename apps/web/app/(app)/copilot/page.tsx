"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, mutate } from "@/lib/api";
import { ago } from "@/lib/format";
import { Page, ErrorBox } from "@/components/ui";

interface Answer { summary: string; root_cause: string; evidence: string[]; affected_resources: string[]; confidence: number; remediation: string[]; relevant_logs: string[]; investigate_commands: string[]; what_changed: string[] }

function AnswerCard({ a }: { a: Answer }) {
  const conf = Math.round(a.confidence * 100);
  return (
    <div className="rounded-lg border border-line bg-panel">
      <div className="border-b border-line px-4 py-3"><p className="font-medium">{a.summary}</p><p className="mt-1 text-sm"><span className="text-mute">Likely root cause:</span> {a.root_cause}</p>
        <div className="mt-2 flex items-center gap-2 text-xs text-mute"><span>Confidence</span><span className="h-1.5 w-32 rounded bg-line"><span className={`block h-full rounded ${conf >= 70 ? "bg-ok" : conf >= 40 ? "bg-warn" : "bg-bad"}`} style={{ width: `${conf}%` }} /></span><span>{conf}%</span></div></div>
      <div className="grid gap-px bg-line sm:grid-cols-2">
        {([["Evidence", a.evidence], ["What changed", a.what_changed], ["Affected", a.affected_resources], ["Recommended actions", a.remediation]] as [string, string[]][]).map(([t, xs]) => xs?.length > 0 && (
          <div key={t} className="bg-panel p-4"><h3 className="mb-1 text-xs font-medium text-mute">{t}</h3><ul className="space-y-1 text-sm">{xs.map((x, i) => <li key={i}>{t === "Recommended actions" ? `${i + 1}. ` : "· "}{x}</li>)}</ul></div>))}
      </div>
      {(a.investigate_commands?.length > 0 || a.relevant_logs?.length > 0) && <div className="border-t border-line p-4">
        {a.investigate_commands?.length > 0 && <><h3 className="mb-1 text-xs font-medium text-mute">Commands to investigate (never run automatically)</h3><pre className="mb-3 overflow-auto rounded-md bg-bg p-3 font-mono text-xs">{a.investigate_commands.join("\n")}</pre></>}
        {a.relevant_logs?.length > 0 && <><h3 className="mb-1 text-xs font-medium text-mute">Relevant log lines</h3><pre className="overflow-auto rounded-md bg-bg p-3 font-mono text-xs text-bad">{a.relevant_logs.join("\n")}</pre></>}</div>}
    </div>
  );
}

function CopilotInner() {
  const sp = useSearchParams();
  const jobId = sp.get("job_id") ?? undefined; const incidentId = sp.get("incident_id") ?? undefined;
  const [q, setQ] = useState(sp.get("q") ?? "");
  const sug = useQuery({ queryKey: ["copilot-sug"], queryFn: () => api<{ questions: string[] }>("/api/v1/copilot/suggestions") });
  const hist = useQuery({ queryKey: ["copilot-hist"], queryFn: () => api<any[]>("/api/v1/copilot/history?limit=10") });
  const ask = useMutation({ mutationFn: (question: string) => mutate("/api/v1/copilot/ask", "post", { body: { question, job_id: jobId, incident_id: incidentId } }) as Promise<{ answer: Answer; context: any; model: string; latency_ms: number }>, onSuccess: () => hist.refetch() });
  return (
    <Page title="AI Copilot">
      <p className="mb-4 text-sm text-mute">Answers come only from your telemetry (runs, logs, host metrics, Kubernetes events, deploys). Secrets are redacted before anything reaches the model. Nothing is executed on your systems.{(jobId || incidentId) && <> Scoped to {jobId ? <Link className="underline" href={`/jobs/${jobId}`}>this job</Link> : <Link className="underline" href={`/incidents/${incidentId}`}>this incident</Link>}.</>}</p>
      <form className="mb-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (q) ask.mutate(q); }}>
        <input className="flex-1 rounded-md border border-line bg-panel px-3 py-2" placeholder="Why did backup-prod fail?" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn btn-primary" disabled={!q || ask.isPending}>{ask.isPending ? "Analysing…" : "Ask"}</button></form>
      <div className="mb-6 flex flex-wrap gap-2">{sug.data?.questions.map((s) => <button key={s} className="btn text-xs" onClick={() => { setQ(s); ask.mutate(s); }}>{s}</button>)}</div>
      {ask.error && <div className="mb-4"><ErrorBox error={ask.error} /></div>}
      {ask.isPending && <div className="mb-4 space-y-2"><div className="skeleton h-16" /><div className="skeleton h-32" /></div>}
      {ask.data && <><AnswerCard a={ask.data.answer} /><p className="mt-2 text-xs text-mute">Looked at {ask.data.context.jobs?.join(", ")} · {ask.data.context.executions} runs · {ask.data.context.metrics_points} metric points · {ask.data.model} · {ask.data.latency_ms} ms</p></>}
      {hist.data && hist.data.length > 0 && <section className="mt-10"><h2 className="mb-2 text-sm font-medium">Earlier questions</h2><div className="tbl rounded-lg border border-line">{hist.data.map((h) => (
        <details key={h.id} className="border-b border-line last:border-0"><summary className="row cursor-pointer grid-cols-[1fr_auto] list-none"><span>{h.question}</span><span className="text-xs text-mute">{ago(h.created_at)}</span></summary><div className="p-3"><AnswerCard a={h.answer} /></div></details>))}</div></section>}
    </Page>
  );
}
export default function CopilotPage() { return <Suspense><CopilotInner /></Suspense>; }
