"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Logo } from "@/components/brand";
import { api } from "@/lib/api";
import { authLoginHref } from "@/lib/auth-nav";

const API = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
/** Same host as the API on WeCrew; local compose uses :8010 for ingest. */
const INGEST = API.includes("localhost") || API.includes("127.0.0.1")
  ? API.replace(":8000", ":8010")
  : API;
const ORG_KEY = "jw_pending_org";

const STEPS = [
  { title: "Workspace", blurb: "Name the place your jobs will live." },
  { title: "Method", blurb: "Pick how signals reach JobWatch." },
  { title: "Install", blurb: "Wire the first heartbeat or agent." },
  { title: "Detect", blurb: "Wait for the first run to land." },
  { title: "Alerts", blurb: "Optional — route failures somewhere human." },
  { title: "Done", blurb: "Open the operations desk." },
] as const;

const METHODS = [
  ["heartbeat", "Heartbeat URL", "One curl at the end of any script or pipeline. Works anywhere."],
  ["agent", "Linux agent", "Discovers crontabs and systemd timers; captures exit codes and logs."],
  ["api", "API / webhook", "POST JSON with status, duration, and exit code from your own code."],
  ["k8s", "Kubernetes", "Helm chart for CronJobs. Prefer heartbeat today if you need a signal immediately."],
] as const;

const HERO: Record<number, [string, string]> = {
  1: ["Name the workspace", "Usually your company or team. You can add more later."],
  2: ["How jobs report in", "Agent, Helm, or a single URL — pick what fits how you already run work."],
  3: ["Connect the first signal", "We create a job or an install command. You run it once."],
  4: ["Waiting on the wire", "This page refreshes itself. Skip if you will ping later."],
  5: ["Who gets paged", "Slack, email, or a webhook. Easy to finish from Settings afterward."],
  6: ["You’re on the desk", "Missed runs and failures show on Overview first."],
};

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* ignore */ }
  };
  return (
    <div className="mt-4 overflow-hidden rounded-2xl border border-line bg-bg">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
        <span className="text-xs font-medium text-mute">{label}</span>
        <button type="button" className="text-xs font-semibold text-accent hover:underline" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-auto px-4 py-3 font-mono text-xs leading-relaxed text-ink whitespace-pre-wrap break-all">{text}</pre>
    </div>
  );
}

function StepRail({ step }: { step: number }) {
  return (
    <ol className="mb-8 flex items-center gap-1.5" aria-label="Onboarding progress">
      {STEPS.map((s, i) => {
        const n = i + 1;
        const done = n < step;
        const current = n === step;
        return (
          <li key={s.title} className="flex flex-1 flex-col items-center gap-1.5" aria-current={current ? "step" : undefined}>
            <div className="flex w-full items-center">
              <span
                className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold ${
                  done ? "bg-ok text-white" : current ? "bg-accent text-white" : "bg-line text-mute"
                }`}
              >
                {done ? "✓" : n}
              </span>
              {n < STEPS.length && <span className={`mx-1 h-0.5 flex-1 rounded ${done ? "bg-ok/40" : "bg-line"}`} />}
            </div>
            <span className={`hidden text-[10px] font-medium sm:block ${current ? "text-accent" : "text-mute"}`}>{s.title}</span>
          </li>
        );
      })}
    </ol>
  );
}

function OnboardingInner() {
  const params = useSearchParams();
  const sess = useQuery({
    queryKey: ["session"],
    queryFn: () => api<{ user: { email: string }; org_id: string | null }>("/auth/session"),
    retry: false,
  });
  const [step, setStep] = useState(1);
  const [org, setOrg] = useState("");
  const [method, setMethod] = useState<string>("heartbeat");
  const [job, setJob] = useState<{ heartbeat_token?: string; name?: string } | null>(null);
  const [tok, setTok] = useState<{ install?: string; token?: string } | null>(null);

  useEffect(() => {
    const fromQuery = params.get("org");
    let fromStore = "";
    try { fromStore = sessionStorage.getItem(ORG_KEY) ?? ""; } catch { /* ignore */ }
    const seed = (fromQuery || fromStore || "").trim();
    if (seed) setOrg(seed);
  }, [params]);

  const createOrg = useMutation({
    mutationFn: () => api<{ org_id: string }>("/auth/orgs", { method: "POST", body: JSON.stringify({ name: org }) }),
    onSuccess: () => {
      try { sessionStorage.removeItem(ORG_KEY); } catch { /* ignore */ }
      sess.refetch();
      setStep(2);
    },
  });
  const mkJob = useMutation({
    mutationFn: async () => {
      const ws = await api<{ id: string }[]>("/api/v1/workspaces");
      return api<{ heartbeat_token?: string; name?: string }>("/api/v1/jobs", {
        method: "POST",
        body: JSON.stringify({ workspace_id: ws[0].id, name: "my-first-job", grace_s: 300 }),
      });
    },
    onSuccess: (j) => { setJob(j); setStep(4); },
  });
  const mkTok = useMutation({
    mutationFn: () => api<{ install?: string; token?: string }>("/api/v1/agents/bootstrap-token", { method: "POST", body: JSON.stringify({}) }),
    onSuccess: (t) => { setTok(t); setStep(4); },
  });
  const jobs = useQuery({
    queryKey: ["jobs", "onb"],
    queryFn: () => api<{ items: { last_run_at?: string; source?: string; name: string }[] }>("/api/v1/jobs?limit=5"),
    enabled: step === 4,
    refetchInterval: 3000,
  });
  const detected = (jobs.data?.items ?? []).filter((j) => j.last_run_at || j.source === "agent");

  useEffect(() => { if (sess.data?.org_id && step === 1) setStep(2); }, [sess.data?.org_id, step]);

  const [heroTitle, heroBody] = HERO[step] ?? HERO[1];

  if (sess.isError) {
    return (
      <Shell heroTitle="Sign in to continue" heroBody="Onboarding needs an account session before a workspace can be created.">
        <h2 className="text-2xl font-semibold text-accent">You’re signed out</h2>
        <p className="mt-2 text-sm text-mute">Continue with your organisation account, then we’ll pick up setup.</p>
        <button type="button" className="btn btn-primary mt-6 w-full py-2.5" onClick={() => window.location.assign(authLoginHref("/onboarding"))}>
          Sign in
        </button>
      </Shell>
    );
  }

  const pingCmd = job?.heartbeat_token
    ? (method === "api"
      ? `curl -X POST ${INGEST}/api/v1/heartbeat/${job.heartbeat_token} \\\n  -H 'Content-Type: application/json' \\\n  -d '{"status":"success","duration_ms":1240,"exit_code":0}'`
      : `curl -fsS ${INGEST}/ping/${job.heartbeat_token}`)
    : "";

  const helmCmd = tok?.token
    ? `helm repo add cronsentinel https://charts.example.com\nhelm install cronsentinel-agent cronsentinel/agent \\\n  --set server=${INGEST} \\\n  --set bootstrapToken=${tok.token}`
    : `helm repo add cronsentinel https://charts.example.com\nhelm install cronsentinel-agent cronsentinel/agent \\\n  --set server=${INGEST} \\\n  --set bootstrapToken=<bootstrap-token>`;

  return (
    <Shell heroTitle={heroTitle} heroBody={heroBody} step={step}>
      <StepRail step={step} />

      {step === 1 && (
        <>
          <h2 className="text-2xl font-semibold text-accent">Name your workspace</h2>
          <p className="mt-1 text-sm text-mute">Shown on the desk and status pages. Change it later in Settings.</p>
          <label className="mt-5 block text-sm font-medium">
            Company or team
            <input
              className="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2.5"
              placeholder="Acme Ops"
              value={org}
              onChange={(e) => setOrg(e.target.value)}
              autoComplete="organization"
            />
          </label>
          <button className="btn btn-primary mt-5 w-full py-2.5" disabled={!org.trim() || createOrg.isPending} onClick={() => createOrg.mutate()}>
            {createOrg.isPending ? "Creating…" : "Create workspace"}
          </button>
          {createOrg.error && <p className="mt-3 text-sm text-bad" role="alert">{(createOrg.error as Error).message}</p>}
        </>
      )}

      {step === 2 && (
        <>
          <h2 className="text-2xl font-semibold text-accent">How do you want to monitor?</h2>
          <p className="mt-1 text-sm text-mute">You can add other methods later from Servers &amp; agents.</p>
          <div className="mt-5 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Monitoring method">
            {METHODS.map(([k, t, d]) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={method === k}
                onClick={() => setMethod(k)}
                className={`rounded-2xl border p-4 text-left transition ${method === k ? "border-accent bg-accent/5 ring-1 ring-accent/30" : "border-line hover:border-accent/30"}`}
              >
                <div className="font-semibold text-accent">{t}</div>
                <div className="mt-1 text-sm text-mute">{d}</div>
              </button>
            ))}
          </div>
          <button className="btn btn-primary mt-5 w-full py-2.5" onClick={() => setStep(3)}>Continue</button>
        </>
      )}

      {step === 3 && (
        <>
          <h2 className="text-2xl font-semibold text-accent">Install</h2>
          {method === "agent" && (
            <>
              <p className="mt-1 text-sm text-mute">We’ll mint a one-time <strong className="font-medium text-ink">bootstrap token</strong> for the Linux installer — not the same as a heartbeat ping token.</p>
              <button className="btn btn-primary mt-5 w-full py-2.5" disabled={mkTok.isPending} onClick={() => mkTok.mutate()}>
                {mkTok.isPending ? "Generating…" : "Generate install command"}
              </button>
            </>
          )}
          {method === "k8s" && (
            <>
              <p className="mt-1 text-sm text-mute">Helm needs a <strong className="font-medium text-ink">bootstrap token</strong>. Or start with a heartbeat job now.</p>
              <CopyBlock label="Helm (placeholder until you generate a token)" text={helmCmd} />
              <div className="mt-4 flex flex-col gap-2 sm:flex-row">
                <button type="button" className="btn btn-primary flex-1 py-2.5" onClick={() => setMethod("heartbeat")}>Use heartbeat instead</button>
                <button type="button" className="btn flex-1 py-2.5" disabled={mkTok.isPending} onClick={() => mkTok.mutate()}>
                  Generate bootstrap token
                </button>
              </div>
            </>
          )}
          {(method === "heartbeat" || method === "api") && (
            <>
              <p className="mt-1 text-sm text-mute">Creates a job and a permanent <strong className="font-medium text-ink">heartbeat token</strong> for that job’s curl URL.</p>
              <button className="btn btn-primary mt-5 w-full py-2.5" disabled={mkJob.isPending} onClick={() => mkJob.mutate()}>
                {mkJob.isPending ? "Creating…" : "Create my first job"}
              </button>
            </>
          )}
        </>
      )}

      {step === 4 && (
        <>
          <h2 className="text-2xl font-semibold text-accent">Detect scheduled jobs</h2>
          <p className="mt-1 text-sm text-mute">Run the command once. This page updates when JobWatch sees traffic.</p>
          {tok?.install && <CopyBlock label="Agent install (bootstrap token — one-time)" text={tok.install} />}
          {tok?.token && !tok.install && <CopyBlock label="Helm with bootstrap token" text={helmCmd} />}
          {job && <CopyBlock label={`Heartbeat ping for job “${job.name ?? "my-first-job"}”`} text={pingCmd} />}
          <div className={`mt-4 rounded-xl px-4 py-3 text-sm ${detected.length ? "bg-ok/10 text-ok" : "bg-bg text-mute"}`}>
            {detected.length
              ? `✓ ${detected.length} job${detected.length > 1 ? "s" : ""} detected: ${detected.map((j) => j.name).join(", ")}`
              : "Waiting for the first signal…"}
          </div>
          <div className="mt-5 flex flex-col gap-2 sm:flex-row">
            <button type="button" className="btn btn-primary flex-1 py-2.5" disabled={!detected.length} onClick={() => setStep(5)}>Continue</button>
            <button type="button" className="btn flex-1 py-2.5" onClick={() => setStep(5)}>Skip for now</button>
          </div>
        </>
      )}

      {step === 5 && (
        <>
          <h2 className="text-2xl font-semibold text-accent">Where should alerts go?</h2>
          <p className="mt-1 text-sm text-mute">Add Slack, email, or a webhook, then a rule such as “any job failed”.</p>
          <div className="mt-5 flex flex-col gap-2 sm:flex-row">
            <Link className="btn btn-primary flex-1 py-2.5 text-center" href="/alerting">Set up alerting</Link>
            <button type="button" className="btn flex-1 py-2.5" onClick={() => setStep(6)}>Skip</button>
          </div>
        </>
      )}

      {step === 6 && (
        <>
          <h2 className="text-2xl font-semibold text-accent">You’re monitoring</h2>
          <p className="mt-1 text-sm text-mute">Missed runs, failures, and slow-downs land on Overview first.</p>
          <Link className="btn btn-primary mt-5 inline-flex w-full py-2.5" href="/">Open the dashboard</Link>
        </>
      )}

      {sess.data?.user?.email && (
        <p className="mt-8 text-center text-xs text-mute">Signed in as {sess.data.user.email}</p>
      )}
    </Shell>
  );
}

function Shell({
  children,
  heroTitle,
  heroBody,
  step,
}: {
  children: React.ReactNode;
  heroTitle: string;
  heroBody: string;
  step?: number;
}) {
  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="hero-band relative hidden overflow-hidden text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <Logo light />
        <div className="relative max-w-md">
          {step != null && (
            <p className="text-sm font-medium uppercase tracking-[0.18em] text-white/70">Setup · step {step} of {STEPS.length}</p>
          )}
          <h1 className="mt-3 text-4xl font-semibold leading-tight">{heroTitle}</h1>
          <p className="mt-4 text-white/80">{heroBody}</p>
        </div>
        <p className="relative text-sm text-white/70">WeCrew JobWatch</p>
      </section>
      <section className="flex items-center justify-center bg-bg px-5 py-12">
        <div className="card w-full max-w-lg p-8">
          <div className="mb-6 lg:hidden"><Logo /></div>
          {children}
        </div>
      </section>
    </main>
  );
}

export default function Onboarding() {
  return (
    <Suspense
      fallback={
        <main className="grid min-h-screen place-items-center bg-bg">
          <div className="skeleton h-10 w-48" />
        </main>
      }
    >
      <OnboardingInner />
    </Suspense>
  );
}
