"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { Page, Skeleton, ErrorBox, Empty } from "@/components/ui";

interface Agent { id: string; kind: string; name: string; host_id: string; version: string | null; status: string; last_seen_at: string | null; skew_ms: number; revoked_at: string | null; jobs: number }

export default function AgentsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["agents"], queryFn: () => api<Agent[]>("/api/v1/agents") });
  const [tok, setTok] = useState<{ token: string; install: string } | null>(null);
  const [rot, setRot] = useState<string | null>(null);
  const mint = useMutation({ mutationFn: () => api<{ token: string; install: string }>("/api/v1/agents/bootstrap-token", { method: "POST", body: JSON.stringify({}) }), onSuccess: setTok });
  const rotate = useMutation({ mutationFn: (id: string) => api<{ agent_key: string }>(`/api/v1/agents/${id}/rotate`, { method: "POST" }), onSuccess: (d) => { setRot(d.agent_key); qc.invalidateQueries({ queryKey: ["agents"] }); } });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/api/v1/agents/${id}/revoke`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }) });
  const stale = (a: Agent) => a.last_seen_at && Date.now() - new Date(a.last_seen_at).getTime() > 5 * 60e3;
  return (
    <Page title="Servers & agents" actions={<button className="btn btn-primary" onClick={() => mint.mutate()}>Install an agent</button>}>
      {tok && <div className="mb-4 rounded-lg border border-line bg-panel p-4 text-sm">
        <p className="mb-2">Run this on the server as root. The token works once and expires in 24 hours.</p>
        <pre className="overflow-auto rounded-md border border-line bg-bg p-3 font-mono text-xs">{tok.install}</pre>
        <p className="mt-2 text-mute">The agent discovers crontabs and systemd timers within 5 minutes. Wrap jobs with <code className="font-mono">cs-run -- cmd</code> to capture exit codes and logs.</p></div>}
      {rot && <div className="mb-4 rounded-md border border-warn/40 bg-warn/5 p-3 text-sm">New key (shown once): <code className="font-mono">{rot}</code> — put it in <code className="font-mono">/etc/cronsentinel/agent.json</code> and restart the agent.</div>}
      {q.error ? <ErrorBox error={q.error} /> : q.isLoading ? <Skeleton /> : q.data!.length === 0
        ? <Empty title="No agents yet" hint="Install the Linux agent to auto-discover cron jobs and collect host metrics." action={<button className="btn btn-primary" onClick={() => mint.mutate()}>Get install command</button>} />
        : <div className="tbl rounded-lg border border-line">
            <div className="row grid-cols-[minmax(0,2fr)_80px_100px_120px_70px_80px_160px] th"><span>Host</span><span>Kind</span><span>Version</span><span>Last seen</span><span>Skew</span><span>Jobs</span><span /></div>
            {q.data!.map((a) => (
              <div key={a.id} className="row grid-cols-[minmax(0,2fr)_80px_100px_120px_70px_80px_160px]">
                <div className="min-w-0"><div className="flex items-center gap-2 font-medium"><span className={`dot ${a.revoked_at ? "dot-paused" : stale(a) ? "dot-late" : "dot-healthy"}`} />{a.name}</div><div className="truncate font-mono text-xs text-mute">{a.host_id}</div></div>
                <span className="text-mute">{a.kind}</span><span className="font-mono text-xs">{a.version ?? "—"}</span><span className="text-mute">{ago(a.last_seen_at)}</span>
                <span className={`font-mono text-xs ${Math.abs(a.skew_ms) > 5000 ? "text-warn" : "text-mute"}`}>{a.skew_ms} ms</span><span>{a.jobs}</span>
                <div className="flex gap-1">{!a.revoked_at && <><button className="btn" onClick={() => rotate.mutate(a.id)}>Rotate key</button><button className="btn text-bad" onClick={() => confirm(`Revoke ${a.name}? It stops reporting immediately.`) && revoke.mutate(a.id)}>Revoke</button></>}{a.revoked_at && <span className="text-xs text-mute">revoked</span>}</div>
              </div>))}
          </div>}
    </Page>
  );
}
