"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, mutate } from "@/lib/api";
import { Page, ErrorBox, Empty } from "@/components/ui";
import { ago } from "@/lib/format";

type Dest = { id: string; name: string; kind: string; url: string; has_secret: boolean; event_types: string[]; enabled: boolean;
  consecutive_failures: number; disabled_reason: string | null; sent_24h: number; failed_24h: number };
type Delivery = { id: number; destination: string; signal_id: string; event_type: string; status: string; attempts: number; last_error: string | null; response_code: number | null; created_at: string };

export default function IntegrationsPage() {
  const qc = useQueryClient();
  const schema = useQuery({ queryKey: ["sig-schema"], queryFn: () => api<any>("/api/v1/integrations/schema") });
  const dests = useQuery({ queryKey: ["sig-dests"], queryFn: () => api<Dest[]>("/api/v1/integrations/destinations") });
  const deliveries = useQuery({ queryKey: ["sig-deliveries"], queryFn: () => api<Delivery[]>("/api/v1/integrations/deliveries?limit=50"), refetchInterval: 15000 });
  const [f, setF] = useState({ name: "", url: "", secret: "", event_types: [] as string[] });
  const [testRes, setTestRes] = useState<Record<string, string>>({});
  const create = useMutation({ mutationFn: () => mutate("/api/v1/integrations/destinations", "post", { body: { ...f, secret: f.secret || null } }),
    onSuccess: () => { setF({ name: "", url: "", secret: "", event_types: [] }); qc.invalidateQueries({ queryKey: ["sig-dests"] }); } });
  const del = useMutation({ mutationFn: (id: string) => mutate("/api/v1/integrations/destinations/{dest_id}", "delete", { path: { dest_id: id } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["sig-dests"] }) });
  const test = useMutation({ mutationFn: (id: string) => mutate("/api/v1/integrations/destinations/{dest_id}/test", "post", { path: { dest_id: id } }) as Promise<any>,
    onSuccess: (r, id) => setTestRes((t) => ({ ...t, [id]: r.ok ? `OK (${r.status_code})` : `Failed: ${r.error ?? r.status_code}` })) });
  const inp = "mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-sm";
  const types: string[] = schema.data?.event_types ?? [];
  return (
    <Page title="Integrations">
      <div className="max-w-3xl space-y-10 text-sm">
        <p className="text-mute">Push JobWatch signals to AEGIS, your data platform, or any HTTPS endpoint. This is the only way telemetry leaves JobWatch — nothing downstream reads the database.</p>
        <section>
          <h2 className="mb-2 font-medium">Signal destinations</h2>
          {dests.error && <ErrorBox error={dests.error} />}
          {dests.data?.length === 0 && <Empty title="No destinations" hint="Add a webhook below. AEGIS OpsGraph consumes jobwatch.signal/1 directly." />}
          {dests.data?.map((d) => (
            <div key={d.id} className={`border-b border-line py-3 ${!d.enabled ? "opacity-70" : ""}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div><b>{d.name}</b> <span className="ml-1 rounded bg-panel2 px-1.5 py-0.5 font-mono text-xs">{d.kind}</span>
                  {!d.enabled && <span className="ml-2 rounded bg-bad/10 px-1.5 py-0.5 text-xs text-bad">disabled</span>}
                  <div className="mt-0.5 font-mono text-xs text-mute">{d.url}{d.has_secret && " · signed"}</div></div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-mute">24h: <span className="text-ok">{d.sent_24h} sent</span>{d.failed_24h > 0 && <> · <span className="text-bad">{d.failed_24h} failed</span></>}</span>
                  <button className="btn" onClick={() => test.mutate(d.id)}>Send test</button>
                  <button className="btn text-bad" onClick={() => confirm("Delete destination?") && del.mutate(d.id)}>Delete</button></div></div>
              {testRes[d.id] && <div className="mt-1 text-xs">{testRes[d.id]}</div>}
              {d.disabled_reason && <div className="mt-1 text-xs text-bad">{d.disabled_reason}</div>}
              <div className="mt-1 text-xs text-mute">{d.event_types.length ? d.event_types.join(", ") : "all event types"}</div>
            </div>))}
          <div className="mt-3 rounded-lg border border-line bg-panel p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label>Name<input className={inp} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="AEGIS OpsGraph" /></label>
              <label>Endpoint URL<input className={`${inp} font-mono`} value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://aegis.example.com/ingest/jobwatch" /></label>
              <label className="sm:col-span-2">Signing secret <span className="text-mute">(optional, ≥16 chars; HMAC sent as X-JobWatch-Signature)</span>
                <input className={`${inp} font-mono`} type="password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></label></div>
            <div className="mt-3">Event types <span className="text-mute">(none selected = all)</span>
              <div className="mt-1 flex flex-wrap gap-2">{types.map((t) => <label key={t} className="flex items-center gap-1 font-mono text-xs">
                <input type="checkbox" checked={f.event_types.includes(t)} onChange={(e) => setF({ ...f, event_types: e.target.checked ? [...f.event_types, t] : f.event_types.filter((x) => x !== t) })} />{t}</label>)}</div></div>
            {create.error && <div className="mt-2"><ErrorBox error={create.error} /></div>}
            <button className="btn btn-primary mt-3" disabled={!f.name || !f.url} onClick={() => create.mutate()}>Add destination</button></div>
        </section>
        <section>
          <h2 className="mb-2 font-medium">Recent deliveries</h2>
          {deliveries.data?.length === 0 && <p className="text-mute">Nothing sent yet.</p>}
          {!!deliveries.data?.length && <div className="tbl"><table className="w-full text-xs"><thead><tr className="text-left text-mute"><th>When</th><th>Destination</th><th>Event</th><th>Status</th><th>Attempts</th><th>Detail</th></tr></thead>
            <tbody>{deliveries.data.map((d) => <tr key={d.id} className="border-t border-line">
              <td className="py-1.5 pr-2 whitespace-nowrap">{ago(d.created_at)}</td><td className="pr-2">{d.destination}</td><td className="pr-2 font-mono">{d.event_type}</td>
              <td className={`pr-2 ${d.status === "sent" ? "text-ok" : d.status === "pending" ? "text-mute" : "text-bad"}`}>{d.status}{d.response_code ? ` ${d.response_code}` : ""}</td>
              <td className="pr-2">{d.attempts}</td><td className="text-mute">{d.last_error ?? ""}</td></tr>)}</tbody></table></div>}
        </section>
        <section>
          <h2 className="mb-2 font-medium">Contract</h2>
          <p className="text-mute">Schema <code className="font-mono">{schema.data?.schema}/{schema.data?.version}</code>. Dedupe on <code className="font-mono">signal_id</code>; deliveries may repeat on retry. Envelope fields are stable; <code className="font-mono">data</code> is additive-only within a major version.</p>
          {schema.data?.example && <pre className="mt-2 max-h-72 overflow-auto rounded-md border border-line bg-panel p-3 font-mono text-[11px]">{JSON.stringify(schema.data.example, null, 2)}</pre>}
        </section>
      </div>
    </Page>
  );
}
