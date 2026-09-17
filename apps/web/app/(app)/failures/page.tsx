"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, type Job } from "@/lib/api";
import { ago } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox, Empty } from "@/components/ui";

const ORDER = ["failed", "timeout", "missed", "late"];
const WHY: Record<string, string> = { failed: "Exited non-zero or reported fail", timeout: "Ran longer than expected + grace", missed: "Never started within 2× grace", late: "Not started yet, inside grace window" };

export default function FailuresPage() {
  const q = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?limit=200") });
  if (q.error) return <Page title="Failures"><ErrorBox error={q.error} /></Page>;
  const bad = (q.data?.items ?? []).filter((j) => ORDER.includes(j.status)).sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || (b.last_run_at ?? "").localeCompare(a.last_run_at ?? ""));
  const recovered = (q.data?.items ?? []).filter((j) => j.status === "recovered");
  return (
    <Page title="Failures">
      {q.isLoading ? <Skeleton /> : bad.length === 0 ? <Empty title="Nothing is broken" hint="Failed, timed-out, missed and late jobs appear here, worst first." /> : (
        <div className="tbl rounded-lg border border-line">
          <div className="row grid-cols-[minmax(0,2fr)_110px_minmax(0,2fr)_110px_90px] th"><span>Job</span><span>State</span><span>Why it's here</span><span>Since</span><span /></div>
          {bad.map((j) => (
            <div key={j.id} className="row grid-cols-[minmax(0,2fr)_110px_minmax(0,2fr)_110px_90px]">
              <div className="min-w-0"><Link href={`/jobs/${j.id}`} className="truncate font-medium hover:underline">{j.name}</Link><div className="truncate text-xs text-mute">{j.schedule_human ?? "heartbeat"}</div></div>
              <Status s={j.status} /><span className="text-mute">{WHY[j.status]}</span><span className="text-mute">{ago(j.last_run_at ?? j.next_expected_at)}</span>
              <Link href={`/logs?job_id=${j.id}&stream=stderr`} className="btn justify-center">stderr</Link>
            </div>))}
        </div>)}
      {recovered.length > 0 && (
        <><h2 className="mb-2 mt-8 text-sm font-medium">Recovered — one good run since failing</h2>
          <div className="tbl rounded-lg border border-line">{recovered.map((j) => (
            <Link key={j.id} href={`/jobs/${j.id}`} className="row grid-cols-[1fr_110px_110px]"><span className="font-medium">{j.name}</span><Status s={j.status} /><span className="text-mute">{ago(j.last_run_at)}</span></Link>))}</div></>)}
    </Page>
  );
}
