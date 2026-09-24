"use client";
// R33: /product — the feature tour, in the shape of a Datadog product page: hero, a sticky anchor
// bar, then one section per capability (heading, one-liner, four bullets, a link into the app,
// and a product visual), alternating sides. Every visual is rendered here from the same tokens as
// the app — no images, no third-party hosts (tests/e2e/pages.spec.ts fails on any). Every bullet
// describes something the platform actually does today; nothing here is roadmap.
import Link from "next/link";
import { useEffect, useState } from "react";

type Sec = { id: string; nav: string; h: string; lead: string; bullets: string[]; to: string; toLabel: string; shot: keyof typeof SHOTS };

const SECTIONS: Sec[] = [
  { id: "agents", nav: "Agents", h: "See every scheduled job, wherever it runs", lead: "Linux cron, systemd timers and Kubernetes CronJobs, discovered by an agent or reported with one heartbeat call.",
    bullets: ["Read-only discovery of crontabs and timers — nothing on the host is changed", "Kubernetes CronJobs and their events, including missed schedules and node pressure", "Agent-less mode: a heartbeat URL per job, one curl at the end of a script", "Events buffered on the agent and replayed in order; the server de-duplicates"],
    to: "/agents", toLabel: "Install an agent", shot: "agents" },
  { id: "schedule", nav: "Schedules", h: "Know what should have run, not just what did", lead: "Expected runs are materialised ahead of time, so silence is a signal too.",
    bullets: ["A slot for every scheduled run, with grace and deadline per job", "Late and missed detected from the timeline of what actually arrived", "Runs that landed while a slot did not exist yet are matched, not lost", "A slot nobody could have observed is marked unobserved — never a false missed"],
    to: "/jobs", toLabel: "Open jobs", shot: "schedule" },
  { id: "alerting", nav: "Alerting", h: "Get told once, on the channel you use", lead: "Rules over job state, routed to Slack, Teams, Discord, Telegram, email or a webhook, with repeat intervals and quiet windows.",
    bullets: ["Conditions on state, consecutive failures and runtime, scoped by tag or workspace", "Every send recorded in a ledger you can read back", "Maintenance windows and paused jobs settle their slots as skipped, not missed", "An outage on our side never pages you — those slots are unobserved and say so"],
    to: "/alerting", toLabel: "Configure alerting", shot: "alerting" },
  { id: "incidents", nav: "Incidents", h: "Open, acknowledge, resolve — with the evidence attached", lead: "Failures are correlated into incidents so a bad night is one thread, not forty alerts.",
    bullets: ["Correlation keys group related failures across jobs", "Acknowledge and resolve from the page, with notes on the thread", "Time-to-detect and time-to-resolve on the overview, computed from the record", "Root cause and resolution kept with the incident, not in a chat scrollback"],
    to: "/incidents", toLabel: "See incidents", shot: "incidents" },
  { id: "logs", nav: "Logs", h: "The output of the run that failed, right there", lead: "Wrap a job with cs-run and its stdout, stderr and exit code arrive with the execution.",
    bullets: ["Captured per execution, searchable, retained by plan", "Storage metered per organisation, incrementally — no scan of your logs to bill you", "Log volume shown against your plan before you hit the limit", "Environment variable values are never collected"],
    to: "/logs", toLabel: "Browse logs", shot: "logs" },
  { id: "analytics", nav: "Analytics", h: "Success rate, p95 runtime, top failing — per job, over time", lead: "Enough to answer “which jobs are getting flakier?” without opening a spreadsheet.",
    bullets: ["Overview with today's executions, success rate, MTTD and MTTR", "Slowest and most-failing jobs over seven days", "Per-job duration percentiles from the execution history", "Every number computed from records you can drill into"],
    to: "/analytics", toLabel: "Open analytics", shot: "analytics" },
  { id: "signals", nav: "Signals", h: "Push state changes into your own systems", lead: "Every job state change is a signed signal you can deliver to a webhook or your own NATS broker.",
    bullets: ["HMAC-signed webhooks with an idempotency key on every delivery", "NATS destinations publish to your broker, on your subject prefix", "Failing destinations back off, then auto-disable with a reason", "Internal and link-local targets refused at save time and at connect time"],
    to: "/integrations", toLabel: "Add a destination", shot: "signals" },
  { id: "status", nav: "Status pages", h: "A public page for the jobs your customers depend on", lead: "Pick the jobs, publish a slug, share the link.",
    bullets: ["Public, no login, served from the same platform", "Shows current state and recent history for the selected jobs", "Jobs that disappear are pruned from the page automatically", "One page per audience — several per organisation"],
    to: "/settings", toLabel: "Create a status page", shot: "status" },
  { id: "copilot", nav: "Copilot", h: "Ask what happened, in plain language", lead: "A read-only assistant over your telemetry. It suggests; you decide.",
    bullets: ["Answers from executions, logs, incidents and Kubernetes events", "Never runs a command or changes a system", "Private LLM endpoints by default; external ones only when you allow it", "Hosts, pods and addresses pseudonymised before anything leaves"],
    to: "/copilot", toLabel: "Try the copilot", shot: "copilot" },
  { id: "api", nav: "API", h: "Everything in the UI is an API call you can make", lead: "Typed, documented, and the same one the dashboard uses.",
    bullets: ["OpenAPI spec with response models for every dashboard read", "Frontend types generated from the spec — a wrong request fails at compile time", "API keys per role: viewer, developer, sre, devops, admin, owner", "Self-service export and deletion of your organisation's data"],
    to: "/settings", toLabel: "Create an API key", shot: "api" },
  { id: "security", nav: "Security", h: "Tenant isolation that the database enforces", lead: "Row-level security on every tenant table, and a stack you can run entirely yourself.",
    bullets: ["Postgres row-level security keyed on the organisation, forced for every role", "Outbound requests through an SSRF guard: no private, loopback or metadata targets", "Secrets envelope-encrypted at rest; machine keys hashed", "Self-hosted with the same code: compose or Helm, no third-party callouts"],
    to: "/settings", toLabel: "Security settings", shot: "security" },
];

// ---- product visuals ----------------------------------------------------------------------------
function Win({ title, children, wide }: { title: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={`rounded-xl border border-line bg-panel shadow-sm ${wide ? "" : "max-w-md"} w-full`}>
      <div className="flex items-center gap-1.5 border-b border-line px-3 py-2 text-xs text-mute"><i className="h-2 w-2 rounded-full bg-bad/70" /><i className="h-2 w-2 rounded-full bg-warn/70" /><i className="h-2 w-2 rounded-full bg-ok/70" /><span className="ml-2 font-mono">{title}</span></div>
      <div className="p-3 text-xs">{children}</div>
    </div>
  );
}
const Dot = ({ s }: { s: string }) => <i className={`dot dot-${s} inline-block h-2 w-2 rounded-full`} />;
const Strip = ({ seq }: { seq: string }) => <span className="strip inline-flex gap-px">{seq.split("").map((c, i) => <i key={i} className={{ s: "success", f: "failed", m: "missed", r: "running", u: "" }[c] ?? ""} />)}</span>;
const Row = ({ s, n, r, strip }: { s: string; n: string; r: string; strip?: string }) => (
  <div className="flex items-center gap-2 border-b border-line py-1.5 last:border-0"><Dot s={s} /><span className="flex-1 truncate font-mono">{n}</span>{strip && <Strip seq={strip} />}<span className="text-mute">{r}</span></div>);

const SHOTS = {
  agents: () => <Win title="agents · 3 online">
    <Row s="healthy" n="db-01 · cron agent" r="14 jobs" /><Row s="healthy" n="prod-eks · k8s agent" r="31 CronJobs" /><Row s="healthy" n="etl-worker · systemd" r="6 timers" />
    <div className="mt-2 rounded bg-bg p-2 font-mono text-[11px] text-mute">curl -fsS $JOBWATCH_URL/ping/&lt;token&gt;   # heartbeat, no agent</div></Win>,
  schedule: () => <Win title="nightly-database-backup · slots">
    {[["succeeded", "02:00", "14m 02s"], ["succeeded", "02:00", "13m 48s"], ["late", "02:00", "grace until 02:10"], ["missed", "02:00", "no run seen"], ["unobserved", "02:00", "monitoring gap on our side"], ["pending", "02:00", "next"]]
      .map(([s, t, r], i) => <div key={i} className="flex gap-3 border-b border-line py-1.5 last:border-0"><span className="w-20 font-mono text-mute">{t}</span><span className={`w-24 ${s === "missed" ? "text-warn" : s === "unobserved" ? "text-mute" : s === "late" ? "text-warn" : s === "succeeded" ? "text-ok" : ""}`}>{s}</span><span className="text-mute">{r}</span></div>)}</Win>,
  alerting: () => <Win title="alert rules">
    {[["failure", "any job · failed", "#ops-alerts"], ["consecutive_failures ≥ 3", "tag:billing", "webhook"], ["late", "workspace:prod", "email"]].map(([c, s, ch]) =>
      <div key={c} className="flex items-center gap-2 border-b border-line py-1.5 last:border-0"><span className="rounded bg-bg px-1.5 font-mono">{c}</span><span className="flex-1 text-mute">{s}</span><span>{ch}</span></div>)}
    <div className="mt-2 flex items-center gap-2 rounded border border-line bg-bg p-2 text-mute"><Dot s="missed" />41 slots unobserved during a platform gap — <b className="text-ink">0 alerts sent</b></div></Win>,
  incidents: () => <Win title="INC-118 · open">
    <div className="font-medium">payments/settle-eod failing since 02:04</div><div className="mt-1 text-mute">3 jobs correlated · detected in 1m 20s</div>
    <div className="mt-2 space-y-1 border-l-2 border-line pl-2 text-mute"><div>02:05 opened — consecutive failures</div><div>02:11 acknowledged by priya</div><div>02:38 note: pg pool exhausted, bumped max_connections</div><div className="text-ok">02:41 resolved · MTTR 37m</div></div></Win>,
  logs: () => <Win title="execution · exit 2" wide><pre className="overflow-x-auto rounded bg-bg p-2 font-mono text-[11px] leading-5"><span className="text-mute">02:04:11</span> connecting to db-01:5432
<span className="text-mute">02:04:41</span> <span className="text-bad">FATAL: remaining connection slots are reserved</span>
<span className="text-mute">02:04:41</span> exit 2 · stderr captured · 1.9 KB</pre></Win>,
  analytics: () => <Win title="overview · today">
    <div className="grid grid-cols-3 gap-2 text-center">{[["94.5%", "success"], ["1.4m", "MTTD"], ["42m", "MTTR"]].map(([v, l]) => <div key={l} className="rounded bg-bg p-2"><div className="text-lg font-semibold">{v}</div><div className="text-mute">{l}</div></div>)}</div>
    <svg viewBox="0 0 200 48" className="mt-3 w-full">{[38, 30, 34, 12, 40, 36, 42, 44, 28, 41, 45, 43].map((h, i) => <rect key={i} x={i * 16 + 2} y={48 - h} width="12" height={h} rx="1" className={h < 20 ? "fill-bad/70" : "fill-ok/60"} />)}</svg></Win>,
  signals: () => <Win title="signal destinations">
    <Row s="healthy" n="aegis-webhook · HMAC" r="1,204 sent" /><Row s="healthy" n="nats://broker.internal · acme.signals" r="982 sent" /><Row s="failed" n="old-endpoint" r="auto-disabled" />
    <div className="mt-2 rounded bg-bg p-2 font-mono text-[11px] text-mute">{"{ \"event_type\": \"job.state_changed\", \"signal_id\": \"01J…\", … }"}</div></Win>,
  status: () => <Win title="status.wecrew.in/acme"><div className="flex items-center gap-2 text-ok"><Dot s="healthy" />All scheduled jobs operational</div>
    <Row s="healthy" n="nightly-database-backup" r="" strip="ssssssssssssss" /><Row s="healthy" n="invoice-run" r="" strip="ssssssfssssss" /><Row s="healthy" n="market-data-import" r="" strip="ssssssssssmss" /></Win>,
  copilot: () => <Win title="copilot"><div className="rounded bg-bg p-2">Why did settle-eod fail at 02:04?</div>
    <div className="mt-2 text-mute">Three executions failed with the same stderr (connection slots reserved) on host <span className="font-mono">host-1</span> right after the backup job's peak. Likely pool exhaustion; check max_connections. <span className="italic">Suggestion only — nothing was changed.</span></div></Win>,
  api: () => <Win title="lib/api.ts" wide><pre className="rounded bg-bg p-2 font-mono text-[11px] leading-5"><span className="text-mute">// body, path and response inferred from the OpenAPI spec</span>
await mutate(&quot;/api/v1/jobs/{"{job_id}"}&quot;, &quot;patch&quot;, {"{ path: { job_id }, body: { paused: true } }"});</pre></Win>,
  security: () => <Win title="postgres"><pre className="rounded bg-bg p-2 font-mono text-[11px] leading-5">ALTER TABLE jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY jobs_tenant ON jobs
  USING (org_id = app_org_id());</pre><div className="mt-2 text-mute">Every tenant table. Every role. Checked by a test that scans the catalogue.</div></Win>,
};

export default function ProductPage() {
  const [active, setActive] = useState("agents");
  useEffect(() => {
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && setActive(e.target.id)), { rootMargin: "-40% 0px -55% 0px" });
    SECTIONS.forEach((s) => { const el = document.getElementById(s.id); if (el) io.observe(el); });
    return () => io.disconnect();
  }, []);
  return (
    <main className="min-h-screen bg-bg text-ink">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <Link href="/welcome" className="whitespace-nowrap font-semibold tracking-tight">WeCrew JobWatch</Link>
        <nav className="flex items-center gap-5 whitespace-nowrap text-sm"><Link href="/product" className="font-medium">Product</Link><Link href="/welcome#pricing" className="hidden text-mute hover:text-ink sm:inline">Pricing</Link><Link href="/tools/cron" className="hidden text-mute hover:text-ink sm:inline">Cron checker</Link><Link href="/login" className="btn btn-primary">Get started</Link></nav>
      </header>

      <section className="mx-auto max-w-4xl px-6 pb-12 pt-16 text-center">
        <h1 className="text-4xl font-semibold tracking-tight md:text-5xl">See every scheduled job in one place</h1>
        <p className="mx-auto mt-4 max-w-2xl text-lg text-mute">Your crons, your timers, your CronJobs, your pipelines, your team. Together — and told when one of them didn't run.</p>
        <div className="mt-8 flex justify-center gap-3"><Link href="/login" className="btn btn-primary px-5 py-2.5 text-base">Start free</Link><Link href="/welcome#faq" className="btn px-5 py-2.5 text-base">How it works</Link></div>
      </section>

      <nav className="sticky top-0 z-10 border-y border-line bg-bg/95 backdrop-blur" aria-label="Product sections">
        <div className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-4 py-2 text-sm">{SECTIONS.map((s) =>
          <a key={s.id} href={`#${s.id}`} className={`whitespace-nowrap rounded px-3 py-1 ${active === s.id ? "bg-panel font-medium text-ink shadow-sm" : "text-mute hover:text-ink"}`}>{s.nav}</a>)}</div>
      </nav>

      {SECTIONS.map((s, i) => { const Shot = SHOTS[s.shot]; return (
        <section key={s.id} id={s.id} className={`scroll-mt-16 ${i % 2 ? "bg-panel/40" : ""} border-b border-line`}>
          <div className={`mx-auto grid max-w-6xl items-center gap-10 px-6 py-16 md:grid-cols-2 ${i % 2 ? "md:[&>*:first-child]:order-2" : ""}`}>
            <div><h2 className="text-2xl font-semibold tracking-tight md:text-3xl">{s.h}</h2><p className="mt-3 text-mute">{s.lead}</p>
              <ul className="mt-5 space-y-2">{s.bullets.map((b) => <li key={b} className="flex gap-2"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" /><span>{b}</span></li>)}</ul>
              <Link href={s.to} className="mt-6 inline-block text-sm font-medium text-accent hover:underline">{s.toLabel} →</Link></div>
            <div className="flex justify-center"><Shot /></div>
          </div>
        </section>); })}

      <section className="mx-auto max-w-6xl px-6 py-20 text-center">
        <h2 className="text-3xl font-semibold tracking-tight">Know before your users do.</h2>
        <p className="mt-3 text-mute">Free for five jobs. Self-host it, or let us run it.</p>
        <div className="mt-6 flex justify-center gap-3"><Link href="/login" className="btn btn-primary px-6 py-3 text-base">Start monitoring</Link><Link href="/welcome#pricing" className="btn px-6 py-3 text-base">See pricing</Link></div>
      </section>
      <footer className="border-t border-line px-6 py-6 text-center text-xs text-mute">WeCrew JobWatch · <Link href="/welcome">Home</Link> · <Link href="/tools/cron">Cron checker</Link> · <Link href="/login">Sign in</Link></footer>
    </main>
  );
}
