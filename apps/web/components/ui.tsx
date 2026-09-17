"use client";
import clsx from "clsx";
import { statusLabel } from "@/lib/format";

export const Status = ({ s }: { s: string }) => (
  <span className="inline-flex items-center gap-1.5 whitespace-nowrap"><span className={`dot dot-${s}`} />{statusLabel[s] ?? s}</span>
);

export const Page = ({ title, actions, children }: { title: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode }) => (
  <div className="mx-auto max-w-[1200px] px-6 py-5">
    <div className="mb-4 flex items-center justify-between gap-4"><h1 className="text-lg font-semibold tracking-tight">{title}</h1>{actions}</div>
    {children}
  </div>
);

export const Empty = ({ title, hint, action }: { title: string; hint: string; action?: React.ReactNode }) => (
  <div className="rounded-lg border border-dashed border-line px-6 py-12 text-center">
    <p className="font-medium">{title}</p><p className="mt-1 text-sm text-mute">{hint}</p>{action && <div className="mt-4">{action}</div>}
  </div>
);

export const ErrorBox = ({ error }: { error: unknown }) => (
  <div className="rounded-md border border-bad/40 bg-bad/5 px-4 py-3 text-sm text-bad">{(error as Error)?.message ?? "Something went wrong."}</div>
);

export const Skeleton = ({ rows = 6 }: { rows?: number }) => (
  <div className="space-y-2">{Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton h-9" />)}</div>
);

export const Strip = ({ execs, onPick }: { execs: { id: string; status: string; duration_ms: number | null }[]; onPick?: (id: string) => void }) => {
  const max = Math.max(1, ...execs.map((e) => e.duration_ms ?? 0));
  return (
    <div className="strip" role="img" aria-label={`Last ${execs.length} runs`}>
      {[...execs].reverse().map((e) => (
        <i key={e.id} className={clsx(e.status)} style={{ height: `${Math.max(18, ((e.duration_ms ?? max * 0.3) / max) * 100)}%` }}
           title={`${statusLabel[e.status] ?? e.status}${e.duration_ms != null ? ` · ${Math.round(e.duration_ms / 1000)}s` : ""}`} onClick={() => onPick?.(e.id)} />
      ))}
    </div>
  );
};

const SEV: Record<string, string> = { critical: "bg-bad text-white", high: "bg-bad/15 text-bad", medium: "bg-warn/15 text-warn", low: "bg-mute/15 text-mute" };
export const Sev = ({ s }: { s: string }) => <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium capitalize ${SEV[s] ?? SEV.low}`}>{s}</span>;
