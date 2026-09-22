"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { mutate } from "@/lib/api";
import { ts } from "@/lib/format";

const EXAMPLES: [string, string][] = [["0 2 * * *", "Nightly at 2 AM"], ["*/15 * * * *", "Every 15 minutes"], ["0 9 * * 1-5", "Weekdays at 9 AM"], ["0 0 1 * *", "First of the month"], ["30 4 * * 0", "Sundays 4:30 AM"]];
export default function CronTool() {
  const [expr, setExpr] = useState("0 2 * * *"); const [tz, setTz] = useState("UTC");
  const q = useQuery({ queryKey: ["cron", expr, tz], queryFn: () => mutate("/api/v1/jobs/schedule/preview", "post", { query: { expr, tz } }) as Promise<{ human: string; next: string[] }>, retry: false });
  const fields = expr.trim().split(/\s+/);
  return (
    <main className="mx-auto max-w-2xl px-6 py-14">
      <Link href="/welcome" className="mb-10 flex items-center gap-2 font-semibold"><span className="dot dot-healthy" />WeCrew JobWatch</Link>
      <h1 className="text-xl font-semibold">Cron expression checker</h1>
      <input className="mt-4 w-full rounded-md border border-line bg-panel px-3 py-3 font-mono text-lg tracking-wider" value={expr} onChange={(e) => setExpr(e.target.value)} spellCheck={false} />
      <div className="mt-1 grid grid-cols-5 gap-2 text-center text-xs text-mute">{["minute", "hour", "day of month", "month", "day of week"].map((l, i) => <span key={l} className={fields[i] ? "" : "text-bad"}>{l}</span>)}</div>
      <p className="mt-4 text-lg">{q.isError ? <span className="text-bad">That doesn't parse as a cron expression.</span> : q.data ? `“${q.data.human}”` : "…"}</p>
      <label className="mt-4 block text-sm">Timezone <input className="ml-2 rounded-md border border-line bg-panel px-2 py-1 font-mono text-xs" value={tz} onChange={(e) => setTz(e.target.value)} /></label>
      {q.data && <><h2 className="mt-6 text-sm font-medium">Next 5 runs</h2><ol className="mt-1 rounded-lg border border-line">{q.data.next.map((n) => <li key={n} className="border-b border-line px-4 py-2 font-mono text-sm last:border-0">{ts(n)} UTC</li>)}</ol></>}
      <div className="mt-6 flex flex-wrap gap-2">{EXAMPLES.map(([e, l]) => <button key={e} className="btn" onClick={() => setExpr(e)}><span className="font-mono text-xs">{e}</span><span className="text-mute">{l}</span></button>)}</div>
      <p className="mt-10 text-sm text-mute">Want to know when this schedule <em>doesn't</em> run? <Link href="/login" className="underline">Monitor it with WeCrew JobWatch</Link>.</p>
    </main>
  );
}
