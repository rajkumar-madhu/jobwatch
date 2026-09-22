"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Logo } from "@/components/brand";
import { api } from "@/lib/api";
import { authLoginHref } from "@/lib/auth-nav";

const API = process.env.NEXT_PUBLIC_API_URL;
const INGEST = API?.replace("8000", "8010");
const ORG_KEY = "jw_pending_org";

const METHODS = [
  ["heartbeat", "Heartbeat URL", "Add one curl to any script, pipeline step or container. Works anywhere."],
  ["agent", "Linux agent", "Discovers crontabs and systemd timers, captures exit codes and logs."],
  ["api", "API / webhook", "Post JSON with status, duration and exit code from your own code."],
  ["k8s", "Kubernetes", "Helm chart watches CronJobs across the cluster. Heartbeat works today if you need monitoring now."],
] as const;

function OnboardingInner() {
  const params = useSearchParams();
  const sess = useQuery({ queryKey: ["session"], queryFn: () => api<{ user: { email: string }; org_id: string | null }>("/auth/session"), retry: false });
  const [step, setStep] = useState(1);
  const [org, setOrg] = useState("");
  const [method, setMethod] = useState<string>("heartbeat");
  const [job, setJob] = useState<{ heartbeat_token?: string; name?: string } | null>(null);
  const [tok, setTok] = useState<{ install?: string } | null>(null);

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
    mutationFn: () => api<{ install?: string }>("/api/v1/agents/bootstrap-token", { method: "POST", body: JSON.stringify({}) }),
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

  if (sess.isError) {
    return (
      <Wrap>
        <p>You need to sign in first.</p>
        <button type="button" className="btn btn-primary mt-4" onClick={() => window.location.assign(authLoginHref("/onboarding"))}>Sign in</button>
      </Wrap>
    );
  }

  const STEPS = ["Create workspace", "Choose method", "Install", "Detect jobs", "Alerting", "Done"];

  return (
    <Wrap>
      <ol className="mb-8 flex flex-wrap gap-x-5 gap-y-1 text-xs text-mute" aria-label="Onboarding progress">
        {STEPS.map((s, i) => (
          <li key={s} className={i + 1 === step ? "font-medium text-ink" : i + 1 < step ? "text-ok" : ""} aria-current={i + 1 === step ? "step" : undefined}>
            {i + 1}. {s}
          </li>
        ))}
      </ol>

      {step === 1 && (
        <>
          <h1 className="text-xl font-semibold">Name your workspace</h1>
          <p className="mt-1 text-sm text-mute">Usually your company or team. You can add more later.</p>
          <input className="mt-4 w-full rounded-md border border-line bg-panel px-3 py-2" placeholder="Acme Ops" value={org} onChange={(e) => setOrg(e.target.value)} autoComplete="organization" />
          <button className="btn btn-primary mt-4" disabled={!org.trim() || createOrg.isPending} onClick={() => createOrg.mutate()}>Create workspace</button>
          {createOrg.error && <p className="mt-2 text-sm text-bad">{(createOrg.error as Error).message}</p>}
        </>
      )}

      {step === 2 && (
        <>
          <h1 className="text-xl font-semibold">How do you want to monitor?</h1>
          <div className="mt-4 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Monitoring method">
            {METHODS.map(([k, t, d]) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={method === k}
                onClick={() => setMethod(k)}
                className={`rounded-lg border p-4 text-left ${method === k ? "border-accent bg-accent/5" : "border-line hover:border-mute/60"}`}
              >
                <div className="font-medium">{t}</div>
                <div className="mt-1 text-sm text-mute">{d}</div>
              </button>
            ))}
          </div>
          <button className="btn btn-primary mt-4" onClick={() => setStep(3)}>Continue</button>
        </>
      )}

      {step === 3 && (
        <>
          <h1 className="text-xl font-semibold">Install</h1>
          {method === "agent" && (
            <>
              <p className="mt-1 text-sm text-mute">Generate a one-time token, then run the installer on the server.</p>
              <button className="btn btn-primary mt-4" disabled={mkTok.isPending} onClick={() => mkTok.mutate()}>Generate install command</button>
            </>
          )}
          {method === "k8s" && (
            <>
              <p className="mt-1 text-sm text-mute">You can install the chart when ready, or start with a heartbeat URL today.</p>
              <pre className="mt-3 overflow-auto rounded-md border border-line bg-panel p-3 font-mono text-xs">
{`helm repo add cronsentinel https://charts.example.com
helm install cronsentinel-agent cronsentinel/agent \\
  --set server=${INGEST} --set bootstrapToken=<token>`}
              </pre>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="btn btn-primary" onClick={() => { setMethod("heartbeat"); }}>Create a heartbeat job instead</button>
                <button className="btn" onClick={() => mkTok.mutate()}>I have a bootstrap token — continue</button>
              </div>
            </>
          )}
          {(method === "heartbeat" || method === "api") && (
            <>
              <p className="mt-1 text-sm text-mute">We&apos;ll create a first job and give you its URL.</p>
              <button className="btn btn-primary mt-4" disabled={mkJob.isPending} onClick={() => mkJob.mutate()}>Create my first job</button>
            </>
          )}
        </>
      )}

      {step === 4 && (
        <>
          <h1 className="text-xl font-semibold">Detect scheduled jobs</h1>
          {tok?.install && <pre className="mt-3 overflow-auto rounded-md border border-line bg-panel p-3 font-mono text-xs">{tok.install}</pre>}
          {job && (
            <pre className="mt-3 overflow-auto rounded-md border border-line bg-panel p-3 font-mono text-xs">
              {method === "api"
                ? `curl -X POST ${INGEST}/api/v1/heartbeat/${job.heartbeat_token} \\\n  -H 'Content-Type: application/json' \\\n  -d '{"status":"success","duration_ms":1240,"exit_code":0}'`
                : `# add to the end of your script or pipeline step\ncurl -fsS ${INGEST}/ping/${job.heartbeat_token}`}
            </pre>
          )}
          <p className="mt-3 text-sm">
            {detected.length ? (
              <span className="text-ok">✓ {detected.length} job{detected.length > 1 ? "s" : ""} detected: {detected.map((j) => j.name).join(", ")}</span>
            ) : (
              <span className="text-mute">Waiting for the first signal… this page updates automatically.</span>
            )}
          </p>
          <div className="mt-4 flex gap-2">
            <button className="btn btn-primary" disabled={!detected.length} onClick={() => setStep(5)}>Continue</button>
            <button className="btn" onClick={() => setStep(5)}>Skip for now</button>
          </div>
        </>
      )}

      {step === 5 && (
        <>
          <h1 className="text-xl font-semibold">Where should alerts go?</h1>
          <p className="mt-1 text-sm text-mute">Add Slack, email or a webhook, then a rule such as &quot;any job failed&quot;. You can do this later too.</p>
          <div className="mt-4 flex gap-2">
            <Link className="btn btn-primary" href="/alerting">Set up alerting</Link>
            <button className="btn" onClick={() => setStep(6)}>Skip</button>
          </div>
        </>
      )}

      {step === 6 && (
        <>
          <h1 className="text-xl font-semibold">You&apos;re monitoring.</h1>
          <p className="mt-1 text-sm text-mute">Missed runs, failures and slow-downs will show on the overview first.</p>
          <Link className="btn btn-primary mt-4" href="/">Open the dashboard</Link>
        </>
      )}
    </Wrap>
  );
}

function Wrap({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-xl px-6 py-14">
      <div className="mb-10"><Logo /></div>
      {children}
    </main>
  );
}

export default function Onboarding() {
  return <Suspense><OnboardingInner /></Suspense>;
}
