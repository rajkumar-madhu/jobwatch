"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Area, AreaChart, Bar, BarChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import { dur } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox } from "@/components/ui";

const C = { ok: "rgb(var(--ok))", bad: "rgb(var(--bad))", warn: "rgb(var(--warn))", run: "rgb(var(--run))", mute: "rgb(var(--mute))" };
const fmtT = (t: string) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export default function AnalyticsPage() {
  const [days, setDays] = useState(14); const [sort, setSort] = useState("reliability"); const [period, setPeriod] = useState("weekly");
  const series = useQuery({ queryKey: ["series", days], queryFn: () => api<any>(`/api/v1/analytics/series?days=${days}`) });
  const jobs = useQuery({ queryKey: ["ajobs", days, sort], queryFn: () => api<any[]>(`/api/v1/analytics/jobs?days=${days}&sort=${sort}`) });
  const mt = useQuery({ queryKey: ["mttr", days], queryFn: () => api<any>(`/api/v1/analytics/mttr?days=${days}`) });
  const rep = useQuery({ queryKey: ["report", period], queryFn: () => api<any>(`/api/v1/analytics/report?period=${period}`) });
  if (series.error) return <Page title="Analytics"><ErrorBox error={series.error} /></Page>;
  const pts = (series.data?.points ?? []).map((p: any) => ({ ...p, t: fmtT(p.t), rate: p.executions ? Math.min(100, Math.round(100 * p.ok / p.executions)) : null, p95_s: p.p95_ms ? p.p95_ms / 1000 : null }));
  const r = rep.data;
  return (
    <Page title="Analytics" actions={<div className="flex gap-1" role="toolbar" aria-label="Time range">{[7, 14, 30, 90].map((d) => <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)} className={`rounded-md px-2.5 py-1 text-sm ${days === d ? "bg-ink/10 font-medium" : "text-mute"}`}>{d}d</button>)}</div>}>
      <div className="mb-6 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">{[["Incidents", mt.data?.incidents], ["MTTD", mt.data?.mttd_min != null ? `${mt.data.mttd_min} min` : "—"], ["MTTA", mt.data?.mtta_min != null ? `${mt.data.mtta_min} min` : "—"], ["MTTR", mt.data?.mttr_min != null ? `${mt.data.mttr_min} min` : "—"]].map(([l, v]) => (
        <div key={l as string}><div className="text-mute">{l}</div><div className="text-xl font-semibold tabular-nums">{v ?? "—"}</div></div>))}</div>
      {series.isLoading ? <Skeleton /> : <div className="grid gap-6 lg:grid-cols-2">
        {[["Execution volume & failures", <BarChart data={pts}><XAxis dataKey="t" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} width={36} /><Tooltip /><Bar dataKey="ok" stackId="a" fill={C.ok} name="succeeded" /><Bar dataKey="failed" stackId="a" fill={C.bad} name="failed" /><Bar dataKey="missed" stackId="a" fill={C.warn} name="missed" /></BarChart>],
          ["Success rate %", <AreaChart data={pts}><XAxis dataKey="t" tick={{ fontSize: 11 }} /><YAxis domain={[0, 100]} tick={{ fontSize: 11 }} width={36} /><Tooltip /><Area dataKey="rate" stroke={C.ok} fill={C.ok} fillOpacity={0.15} name="success %" /></AreaChart>],
          ["Duration p50 / p95 (s)", <LineChart data={pts}><XAxis dataKey="t" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} width={36} /><Tooltip /><Line dataKey={(p: any) => p.p50_ms ? p.p50_ms / 1000 : null} stroke={C.run} dot={false} name="p50 s" /><Line dataKey="p95_s" stroke={C.warn} dot={false} name="p95 s" /></LineChart>],
          ["Incidents opened", <BarChart data={(series.data?.incidents ?? []).map((i: any) => ({ ...i, t: fmtT(i.t) }))}><XAxis dataKey="t" tick={{ fontSize: 11 }} /><YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={36} /><Tooltip /><Bar dataKey="n" fill={C.bad} name="incidents" /></BarChart>]].map(([t, chart]) => (
          <section key={t as string}><h2 className="mb-1 text-sm font-medium">{t as string}</h2><div className="h-48 rounded-lg border border-line p-2"><ResponsiveContainer>{chart as any}</ResponsiveContainer></div></section>))}
      </div>}

      <section className="mt-10"><div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-medium">Per job</h2><div className="flex gap-1 text-sm">{[["reliability", "Lowest score"], ["failures", "Most failures"], ["p95", "Slowest"], ["drift", "Duration drift"]].map(([k, l]) => <button key={k} onClick={() => setSort(k)} className={`rounded-md px-2 py-1 ${sort === k ? "bg-ink/10 font-medium" : "text-mute"}`}>{l}</button>)}</div></div>
        <div className="tbl rounded-lg border border-line"><div className="row grid-cols-[minmax(0,2fr)_100px_60px_70px_70px_80px_80px_80px_70px] th"><span>Job</span><span>Status</span><span className="text-right">Score</span><span className="text-right">Runs</span><span className="text-right">Success</span><span className="text-right">p95</span><span className="text-right">Drift</span><span className="text-right">SLA</span><span className="text-right">Est. cost</span></div>
          {jobs.data?.map((j) => (
            <Link key={j.id} href={`/jobs/${j.id}`} className="row grid-cols-[minmax(0,2fr)_100px_60px_70px_70px_80px_80px_80px_70px] text-sm">
              <span className="truncate font-medium">{j.name}</span><Status s={j.status} /><span className={`text-right font-mono ${j.reliability_score < 70 ? "text-bad" : j.reliability_score < 90 ? "text-warn" : ""}`}>{j.reliability_score ?? "—"}</span>
              <span className="text-right font-mono">{j.runs}</span><span className="text-right font-mono">{j.success_rate != null ? `${j.success_rate}%` : "—"}</span><span className="text-right font-mono">{dur(j.p95_ms)}</span>
              <span className={`text-right font-mono ${j.drift_pct > 25 ? "text-warn" : ""}`}>{j.drift_pct != null ? `${j.drift_pct > 0 ? "+" : ""}${j.drift_pct}%` : "—"}</span>
              <span className={`text-right ${j.sla_met === false ? "text-bad" : j.sla_met ? "text-ok" : "text-mute"}`}>{j.sla_target != null ? (j.sla_met ? "met" : "breached") : "—"}</span><span className="text-right font-mono text-mute">${j.est_cost_usd}</span>
            </Link>))}</div>
        <p className="mt-1 text-xs text-mute">Drift compares mean duration of the second half of the window with the first. Cost is compute-hours × $0.05 placeholder.</p></section>

      <section className="mt-10"><div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-medium">Reliability report</h2><div className="flex gap-1 text-sm">{["daily", "weekly", "monthly"].map((k) => <button key={k} onClick={() => setPeriod(k)} className={`rounded-md px-2 py-1 capitalize ${period === k ? "bg-ink/10 font-medium" : "text-mute"}`}>{k}</button>)}</div></div>
        {r && <div className="rounded-lg border border-line bg-panel p-5 text-sm">
          <p className="text-base">{r.runs} runs, <b>{r.success_rate ?? "—"}%</b> succeeded{r.delta_pts != null && <span className={r.delta_pts >= 0 ? "text-ok" : "text-bad"}> ({r.delta_pts >= 0 ? "+" : ""}{r.delta_pts} pts vs previous {period.replace("ly", "")})</span>}. {r.failed} failed, {r.missed} missed, p95 {dur(r.p95_ms)}. {r.incidents} incident{r.incidents === 1 ? "" : "s"}{r.open_incidents ? `, ${r.open_incidents} still open` : ""}.</p>
          <div className="mt-4 grid gap-6 sm:grid-cols-3">{[["Most failures", r.worst_jobs, (x: any) => `${x.failures}`], ["Slowest (p95)", r.slowest_jobs, (x: any) => dur(x.p95_ms)], ["Lowest reliability", r.lowest_reliability, (x: any) => `${x.reliability_score}/100`]].map(([t, xs, f]) => (
            <div key={t as string}><h3 className="mb-1 text-xs font-medium text-mute">{t as string}</h3><ul>{(xs as any[]).map((x) => <li key={x.name} className="flex justify-between py-0.5"><span className="truncate">{x.name}</span><span className="font-mono text-xs">{(f as any)(x)}</span></li>)}{(xs as any[]).length === 0 && <li className="text-mute">none</li>}</ul></div>))}</div>
          <button className="btn mt-4" onClick={() => window.print()}>Print / save as PDF</button></div>}</section>
    </Page>
  );
}
