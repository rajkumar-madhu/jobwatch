"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { ago, dur, ts } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox, Sev } from "@/components/ui";

const KIND: Record<string, string> = { correlated: "Job correlated into this incident", opened: "Incident opened", status_change: "Status changed", notified: "Notification sent", acknowledged: "Acknowledged", resolved: "Resolved", note: "Note" };

export default function IncidentPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["incident", id], queryFn: () => api<any>(`/api/v1/incidents/${id}`) });
  const [note, setNote] = useState(""); const [res, setRes] = useState({ resolution: "", root_cause: "" });
  const inv = () => qc.invalidateQueries({ queryKey: ["incident", id] });
  const ack = useMutation({ mutationFn: () => api(`/api/v1/incidents/${id}/ack`, { method: "POST" }), onSuccess: inv });
  const resolve = useMutation({ mutationFn: () => api(`/api/v1/incidents/${id}/resolve`, { method: "POST", body: JSON.stringify(res) }), onSuccess: inv });
  const addNote = useMutation({ mutationFn: () => api(`/api/v1/incidents/${id}/notes`, { method: "POST", body: JSON.stringify({ text: note }) }), onSuccess: () => { setNote(""); inv(); } });
  if (q.error) return <Page title="Incident"><ErrorBox error={q.error} /></Page>;
  if (!q.data) return <Page title="Incident"><Skeleton /></Page>;
  const { incident: i, timeline, notifications, affected_jobs, executions, downstream_impact } = q.data;
  const impacted = Object.values(downstream_impact ?? {}).flat() as any[];
  return (
    <Page title={<span className="flex items-center gap-3"><Sev s={i.severity} />{i.title}</span>}
          actions={i.status === "open" ? <button className="btn btn-primary" onClick={() => ack.mutate()}>Acknowledge</button> : <span className="text-sm capitalize text-mute">{i.status}</span>}>
      <p className="mb-5 text-sm text-mute">Started {ts(i.started_at)} ({ago(i.started_at)}){i.resolved_at && <> · resolved after {dur(new Date(i.resolved_at).getTime() - new Date(i.started_at).getTime())}</>}{i.last_notified_at && <> · last notified {ago(i.last_notified_at)}</>}</p>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-6">
          <section>
            <h2 className="mb-2 text-sm font-medium">Timeline</h2>
            <ol className="tbl rounded-lg border border-line">{timeline.map((t: any, k: number) => (
              <li key={k} className="row grid-cols-[130px_1fr]"><span className="font-mono text-xs text-mute">{ts(t.ts)}</span>
                <span>{KIND[t.kind] ?? t.kind}{t.payload?.new && <span className="ml-2 text-mute">{t.payload.prev} → {t.payload.new}</span>}{t.payload?.kind && <span className="ml-2 text-mute">via {t.payload.kind} · {t.payload.status}</span>}{t.payload?.text && <span className="ml-2">{t.payload.text}</span>}{t.payload?.auto && <span className="ml-2 text-ok">job recovered</span>}{t.payload?.reason && <span className="ml-2 text-mute">({t.payload.reason})</span>}</span></li>))}</ol>
            <div className="mt-2 flex gap-2"><input className="flex-1 rounded-md border border-line bg-panel px-2 py-1.5 text-sm" placeholder="Add a note for responders…" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Enter" && note && addNote.mutate()} /><button className="btn" disabled={!note} onClick={() => addNote.mutate()}>Add note</button></div>
          </section>
          <section>
            <h2 className="mb-2 text-sm font-medium">Runs since the incident started</h2>
            <div className="tbl rounded-lg border border-line">{executions.length === 0 ? <p className="px-4 py-3 text-sm text-mute">No runs yet.</p> : executions.map((e: any) => (
              <Link key={e.id} href={`/jobs/${e.job_id}`} className="row grid-cols-[130px_1fr_80px_50px]"><span className="font-mono text-xs">{ts(e.scheduled_ts)}</span><Status s={e.status} /><span className="text-right font-mono text-xs">{dur(e.duration_ms)}</span><span className="text-right font-mono text-xs">{e.exit_code ?? "—"}</span></Link>))}</div>
          </section>
          {i.status !== "resolved" && (
            <section className="rounded-lg border border-line bg-panel p-4">
              <h2 className="mb-2 text-sm font-medium">Resolve</h2>
              <input className="mb-2 w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm" placeholder="Root cause (optional)" value={res.root_cause} onChange={(e) => setRes({ ...res, root_cause: e.target.value })} />
              <input className="mb-2 w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm" placeholder="What fixed it" value={res.resolution} onChange={(e) => setRes({ ...res, resolution: e.target.value })} />
              <button className="btn btn-primary" disabled={!res.resolution} onClick={() => resolve.mutate()}>Mark resolved</button>
            </section>)}
          {i.status === "resolved" && (i.root_cause || i.resolution) && <section className="rounded-lg border border-line p-4 text-sm"><p><b>Root cause:</b> {i.root_cause ?? "—"}</p><p><b>Resolution:</b> {i.resolution}</p></section>}
        </div>
        <div className="space-y-6">
          <section><h2 className="mb-2 text-sm font-medium">Affected jobs</h2>
            <div className="tbl rounded-lg border border-line">{affected_jobs.map((j: any) => (
              <Link key={j.id} href={`/jobs/${j.id}`} className="row grid-cols-[1fr_auto]"><span className="font-medium">{j.name}</span><Status s={j.status} /></Link>))}</div></section>
          <section><h2 className="mb-2 text-sm font-medium">Who was notified</h2>
            <div className="tbl rounded-lg border border-line">{notifications.length === 0 ? <p className="px-4 py-3 text-sm text-mute">No notifications sent — check alert rules and channels.</p> : notifications.map((n: any, k: number) => (
              <div key={k} className="row grid-cols-[130px_1fr_70px]"><span className="font-mono text-xs text-mute">{ts(n.sent_at)}</span><span>{n.kind} · {n.name}</span><span className={n.status === "sent" ? "text-ok" : "text-bad"}>{n.status}</span></div>))}</div></section>
          {impacted.length > 0 && <section><h2 className="mb-2 text-sm font-medium">Downstream at risk</h2><div className="tbl rounded-lg border border-line">{impacted.map((u: any) => <Link key={u.id} href={`/jobs/${u.id}`} className="row grid-cols-[1fr_auto]"><span>{u.name}</span><Status s={u.status} /></Link>)}</div></section>}
          {i.correlation_signals?.length > 1 && <section><h2 className="mb-2 text-sm font-medium">Why these were grouped</h2><ul className="rounded-lg border border-line px-4 py-2 text-xs text-mute">{i.correlation_signals.map((sg: any, k: number) => <li key={k} className="py-0.5">{sg.host && `host ${sg.host}`}{sg.cluster_ns && ` · ${sg.cluster_ns.split(":")[1]} namespace`}{sg.deploy_version && ` · deploy ${sg.deploy_version}`}{sg.infra?.length ? ` · ${sg.infra.join(", ")}` : ""}</li>)}</ul></section>}
          <section className="rounded-lg border border-line bg-panel p-4 text-sm"><h2 className="mb-1 font-medium">AI analysis</h2>{i.root_cause ? <p>{i.root_cause}</p> : <p className="text-mute">Ask the copilot why this happened; its root cause is saved here.</p>}
            <Link href={`/copilot?incident_id=${i.id}&q=${encodeURIComponent("Why did this incident happen and what should we do?")}`} className="btn btn-primary mt-3">Analyse with Copilot</Link></section>
        </div>
      </div>
    </Page>
  );
}
