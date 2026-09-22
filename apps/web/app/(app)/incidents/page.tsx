"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, type Incident } from "@/lib/api";
import { ago } from "@/lib/format";
import { Page, Skeleton, ErrorBox, Empty, Sev } from "@/components/ui";


export default function IncidentsPage() {
  const [tab, setTab] = useState<"open" | "acknowledged" | "resolved">("open");
  const q = useQuery({ queryKey: ["incidents", tab], queryFn: () => api<Incident[]>(`/api/v1/incidents?status=${tab}&limit=100`) });
  return (
    <Page title="Incidents">
      <div className="mb-3 flex gap-1" role="tablist" aria-label="Incident status">
        {(["open", "acknowledged", "resolved"] as const).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`rounded-md px-2.5 py-1 text-sm capitalize ${tab === t ? "bg-ink/10 font-medium" : "text-mute hover:text-ink"}`}>{t}</button>
        ))}
      </div>
      {q.error ? <ErrorBox error={q.error} /> : q.isLoading ? <Skeleton /> : q.data!.length === 0
        ? <Empty title={`No ${tab} incidents`} hint="Incidents open automatically when an alert rule fires and close when the job recovers." />
        : <div className="tbl rounded-lg border border-line">
            <div className="row grid-cols-[80px_minmax(0,2fr)_minmax(0,1fr)_120px_120px] th"><span>Severity</span><span>Incident</span><span>Jobs</span><span>Started</span><span>{tab === "resolved" ? "Duration" : "Open for"}</span></div>
            {q.data!.map((i) => {
              const ms = new Date(i.resolved_at ?? Date.now()).getTime() - new Date(i.started_at).getTime();
              return (
                <Link key={i.id} href={`/incidents/${i.id}`} className="row grid-cols-[80px_minmax(0,2fr)_minmax(0,1fr)_120px_120px]">
                  <Sev s={i.severity} /><span className="truncate font-medium">{i.title}</span><span className="truncate text-mute">{i.job_names?.join(", ")}</span>
                  <span className="text-mute">{ago(i.started_at)}</span><span className="font-mono text-xs">{Math.round(ms / 60000)} min</span>
                </Link>);
            })}
          </div>}
    </Page>
  );
}
