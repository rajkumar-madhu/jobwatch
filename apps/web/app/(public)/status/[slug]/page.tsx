"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Logo } from "@/components/brand";
import { api } from "@/lib/api";
import { ago, ts } from "@/lib/format";

type StatusJob = { name: string; status: string; uptime_90d?: number | null; recent?: string[]; last_run_at?: string | null };
type StatusIncident = { title: string; started_at: string; resolved_at?: string | null };
type StatusMaintenance = { starts_at: string; ends_at: string };
type StatusPayload = {
  title: string;
  overall: string;
  jobs?: StatusJob[];
  incidents?: StatusIncident[];
  maintenance?: StatusMaintenance[];
};

const OVERALL: Record<string, [string, string]> = {
  operational: ["All scheduled jobs are running normally", "bg-ok"],
  delayed: ["Some jobs are running late", "bg-warn"],
  degraded: ["Some scheduled jobs have failed", "bg-bad"],
};

export default function StatusPage() {
  const { slug } = useParams<{ slug: string }>();
  const q = useQuery({
    queryKey: ["status", slug],
    queryFn: () => api<StatusPayload>(`/public/status/${slug}`),
    refetchInterval: 30_000,
    retry: false,
  });

  if (q.isError) {
    return (
      <main className="mx-auto max-w-2xl px-6 py-20 text-center">
        <Logo />
        <p className="mt-8 text-mute">This status page doesn&apos;t exist or isn&apos;t public.</p>
        <Link href="/welcome" className="btn mt-6 inline-flex">Back to JobWatch</Link>
      </main>
    );
  }

  if (!q.data) {
    return (
      <main className="mx-auto max-w-2xl px-6 py-14">
        <Logo />
        <div className="mt-8 skeleton h-16" />
      </main>
    );
  }

  const d = q.data;
  const [msg, cls] = OVERALL[d.overall] ?? ["Status unavailable", "bg-mute"];
  const jobs = d.jobs ?? [];
  const incidents = d.incidents ?? [];
  const maintenance = d.maintenance ?? [];

  return (
    <main className="mx-auto max-w-2xl px-6 py-14">
      <div className="mb-8 flex items-center justify-between gap-4">
        <Logo />
        <Link href="/welcome" className="text-sm font-medium text-accent hover:underline">JobWatch home</Link>
      </div>
      <h1 className="text-xl font-semibold">{d.title}</h1>
      <div className={`mt-4 rounded-lg px-4 py-3 text-white ${cls}`}>{msg}</div>
      {maintenance.length > 0 && (
        <div className="mt-3 rounded-lg border border-line px-4 py-3 text-sm">
          Scheduled maintenance: {maintenance.map((m) => `${ts(m.starts_at)} – ${ts(m.ends_at)}`).join("; ")}
        </div>
      )}
      <section className="mt-8 rounded-lg border border-line">
        {jobs.map((j) => (
          <div key={j.name} className="border-b border-line px-4 py-3 last:border-0">
            <div className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 font-medium"><span className={`dot dot-${j.status}`} />{j.name}</span>
              <span className="text-mute">{j.uptime_90d != null ? `${j.uptime_90d}% · 90 days` : "no data"}</span>
            </div>
            <div className="strip mt-2 !h-3" role="img" aria-label={`Recent runs for ${j.name}`}>
              {[...(j.recent ?? [])].reverse().map((s, i) => <i key={i} className={s} style={{ height: "100%" }} />)}
            </div>
            <div className="mt-1 text-xs text-mute">last run {ago(j.last_run_at ?? null)}</div>
          </div>
        ))}
        {jobs.length === 0 && <p className="px-4 py-6 text-center text-sm text-mute">No jobs are published on this page yet.</p>}
      </section>
      <h2 className="mt-8 text-sm font-medium">Incidents, last 30 days</h2>
      {incidents.length === 0 ? (
        <p className="mt-1 text-sm text-mute">None.</p>
      ) : (
        <ul className="mt-1 rounded-lg border border-line">
          {incidents.map((i, k) => (
            <li key={k} className="border-b border-line px-4 py-2 text-sm last:border-0">
              <span className="font-medium">{i.title}</span>
              <span className="ml-2 text-mute">{ts(i.started_at)}{i.resolved_at ? ` — resolved ${ago(i.resolved_at)}` : " — ongoing"}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-10 text-xs text-mute">Powered by WeCrew JobWatch</p>
    </main>
  );
}
