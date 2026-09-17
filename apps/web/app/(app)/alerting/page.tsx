"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { ts } from "@/lib/format";
import { Page, Skeleton, ErrorBox, Empty } from "@/components/ui";

const KINDS: Record<string, { label: string; fields: [string, string][] }> = {
  slack: { label: "Slack", fields: [["webhook_url", "Incoming webhook URL"]] }, teams: { label: "Microsoft Teams", fields: [["webhook_url", "Incoming webhook URL"]] },
  discord: { label: "Discord", fields: [["webhook_url", "Webhook URL"]] }, telegram: { label: "Telegram", fields: [["bot_token", "Bot token"], ["chat_id", "Chat ID"]] },
  webhook: { label: "Webhook", fields: [["url", "URL"], ["secret", "HMAC secret (optional)"]] },
  email: { label: "Email (SMTP)", fields: [["host", "SMTP host"], ["port", "Port"], ["username", "Username"], ["password", "Password"], ["from", "From"], ["to", "To (comma-separated)"]] },
};
const CONDITIONS: [string, string][] = [["failed", "Job failed"], ["missed", "Job missed its schedule"], ["late", "Job is late"], ["runtime_exceeded", "Ran longer than expected"], ["consecutive_failures", "N failures in a row"], ["sla_breach", "Success rate below SLA"], ["recovered", "Job recovered"]];

export default function AlertingPage() {
  const qc = useQueryClient();
  const ch = useQuery({ queryKey: ["channels"], queryFn: () => api<any[]>("/api/v1/alerts/channels") });
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api<any[]>("/api/v1/alerts/rules") });
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: () => api<any[]>("/api/v1/alerts/ledger?limit=30") });
  const [nc, setNc] = useState<{ kind: string; name: string; config: Record<string, string> } | null>(null);
  const [nr, setNr] = useState<{ name: string; condition: string; tags: string; severity: string; channel_ids: string[]; repeat: string; count: string } | null>(null);
  const inv = (k: string) => () => qc.invalidateQueries({ queryKey: [k] });
  const addCh = useMutation({ mutationFn: () => api("/api/v1/alerts/channels", { method: "POST", body: JSON.stringify({ ...nc, config: { ...nc!.config, to: nc!.config.to?.split(",").map((s) => s.trim()) } }) }), onSuccess: () => { setNc(null); inv("channels")(); } });
  const testCh = useMutation({ mutationFn: (id: string) => api(`/api/v1/alerts/channels/${id}/test`, { method: "POST" }), onSuccess: () => setTimeout(inv("ledger"), 2000) });
  const delCh = useMutation({ mutationFn: (id: string) => api(`/api/v1/alerts/channels/${id}`, { method: "DELETE" }), onSuccess: inv("channels") });
  const addRule = useMutation({ mutationFn: () => api("/api/v1/alerts/rules", { method: "POST", body: JSON.stringify({ name: nr!.name, condition: nr!.condition, severity: nr!.severity, channel_ids: nr!.channel_ids,
    scope: nr!.tags ? { tags: nr!.tags.split(",").map((s) => s.trim()) } : {}, params: nr!.condition === "consecutive_failures" ? { count: Number(nr!.count || 3) } : {}, repeat_interval_s: nr!.repeat ? Number(nr!.repeat) * 60 : null }) }), onSuccess: () => { setNr(null); inv("rules")(); } });
  const toggle = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api(`/api/v1/alerts/rules/${id}?enabled=${enabled}`, { method: "PATCH" }), onSuccess: inv("rules") });
  const delRule = useMutation({ mutationFn: (id: string) => api(`/api/v1/alerts/rules/${id}`, { method: "DELETE" }), onSuccess: inv("rules") });
  const inp = "mt-1 w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm";
  const err = addCh.error || addRule.error;
  return (
    <Page title="Alerting">
      {err && <div className="mb-3"><ErrorBox error={err} /></div>}
      <section className="mb-8">
        <div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-medium">Channels</h2><button className="btn" onClick={() => setNc({ kind: "slack", name: "", config: {} })}>Add channel</button></div>
        {nc && <div className="mb-3 rounded-lg border border-line bg-panel p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">Type<select className={inp} value={nc.kind} onChange={(e) => setNc({ ...nc, kind: e.target.value, config: {} })}>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></label>
            <label className="text-sm">Name<input className={inp} value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} placeholder="ops-alerts" /></label>
            {KINDS[nc.kind].fields.map(([k, l]) => <label key={k} className="text-sm">{l}<input className={`${inp} font-mono`} type={/password|token|secret/.test(k) ? "password" : "text"} value={nc.config[k] ?? ""} onChange={(e) => setNc({ ...nc, config: { ...nc.config, [k]: e.target.value } })} /></label>)}
          </div>
          <p className="mt-2 text-xs text-mute">Credentials are encrypted at rest and never shown again.</p>
          <div className="mt-3 flex gap-2"><button className="btn btn-primary" disabled={!nc.name} onClick={() => addCh.mutate()}>Save channel</button><button className="btn" onClick={() => setNc(null)}>Cancel</button></div></div>}
        {ch.isLoading ? <Skeleton rows={2} /> : ch.data!.length === 0 ? <Empty title="No channels" hint="Add Slack, email, Teams, Discord, Telegram or a webhook to receive alerts." /> :
          <div className="tbl rounded-lg border border-line">{ch.data!.map((c) => (
            <div key={c.id} className="row grid-cols-[1fr_100px_100px_170px]"><span className="font-medium">{c.name}</span><span className="text-mute">{KINDS[c.kind]?.label ?? c.kind}</span><span className="text-xs text-mute">{c.rate_per_min}/min</span>
              <div className="flex gap-1"><button className="btn" onClick={() => testCh.mutate(c.id)}>Send test</button><button className="btn text-bad" onClick={() => confirm("Delete channel?") && delCh.mutate(c.id)}>Delete</button></div></div>))}</div>}
      </section>

      <section className="mb-8">
        <div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-medium">Rules</h2><button className="btn" disabled={!ch.data?.length} onClick={() => setNr({ name: "", condition: "failed", tags: "", severity: "high", channel_ids: [], repeat: "30", count: "3" })}>Add rule</button></div>
        {nr && <div className="mb-3 rounded-lg border border-line bg-panel p-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-sm">Name<input className={inp} value={nr.name} onChange={(e) => setNr({ ...nr, name: e.target.value })} placeholder="Prod failures → Slack" /></label>
            <label className="text-sm">When<select className={inp} value={nr.condition} onChange={(e) => setNr({ ...nr, condition: e.target.value })}>{CONDITIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            {nr.condition === "consecutive_failures" ? <label className="text-sm">Failures in a row<input type="number" className={inp} value={nr.count} onChange={(e) => setNr({ ...nr, count: e.target.value })} /></label>
              : <label className="text-sm">Severity<select className={inp} value={nr.severity} onChange={(e) => setNr({ ...nr, severity: e.target.value })}>{["critical", "high", "medium", "low"].map((s) => <option key={s}>{s}</option>)}</select></label>}
            <label className="text-sm">Only jobs tagged (comma-separated, blank = all)<input className={inp} value={nr.tags} onChange={(e) => setNr({ ...nr, tags: e.target.value })} placeholder="prod, db" /></label>
            <label className="text-sm">Repeat every (min, blank = once)<input type="number" className={inp} value={nr.repeat} onChange={(e) => setNr({ ...nr, repeat: e.target.value })} /></label>
            <div className="text-sm">Send to<div className="mt-1 flex flex-wrap gap-2">{ch.data!.map((c) => <label key={c.id} className="flex items-center gap-1"><input type="checkbox" checked={nr.channel_ids.includes(c.id)} onChange={(e) => setNr({ ...nr, channel_ids: e.target.checked ? [...nr.channel_ids, c.id] : nr.channel_ids.filter((x) => x !== c.id) })} />{c.name}</label>)}</div></div>
          </div>
          <div className="mt-3 flex gap-2"><button className="btn btn-primary" disabled={!nr.name || nr.channel_ids.length === 0} onClick={() => addRule.mutate()}>Save rule</button><button className="btn" onClick={() => setNr(null)}>Cancel</button></div></div>}
        {rules.isLoading ? <Skeleton rows={2} /> : rules.data!.length === 0 ? <Empty title="No rules" hint="Without a rule, failures still open incidents but nobody is notified." /> :
          <div className="tbl rounded-lg border border-line">{rules.data!.map((r) => (
            <div key={r.id} className={`row grid-cols-[1fr_180px_120px_100px_150px] ${r.enabled ? "" : "opacity-50"}`}><span className="font-medium">{r.name}</span><span className="text-mute">{CONDITIONS.find(([k]) => k === r.condition)?.[1]}</span>
              <span className="text-xs text-mute">{r.scope?.tags?.length ? `tags: ${r.scope.tags.join(", ")}` : "all jobs"}</span><span className="text-xs capitalize">{r.severity}</span>
              <div className="flex gap-1"><button className="btn" onClick={() => toggle.mutate({ id: r.id, enabled: !r.enabled })}>{r.enabled ? "Disable" : "Enable"}</button><button className="btn text-bad" onClick={() => confirm("Delete rule?") && delRule.mutate(r.id)}>Delete</button></div></div>))}</div>}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium">Recent deliveries</h2>
        {ledger.data?.length === 0 ? <p className="text-sm text-mute">Nothing sent yet.</p> : <div className="tbl rounded-lg border border-line">{ledger.data?.map((l) => (
          <div key={l.id} className="row grid-cols-[130px_1fr_1fr_80px]"><span className="font-mono text-xs text-mute">{ts(l.sent_at)}</span><span>{l.kind} · {l.name}</span><span className="truncate font-mono text-xs text-mute">{l.dedup_key}</span>
            <span className={l.status === "sent" ? "text-ok" : l.status === "rate_limited" ? "text-warn" : "text-bad"} title={l.error ?? ""}>{l.status}</span></div>))}</div>}
      </section>
    </Page>
  );
}
