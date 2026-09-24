"use client";
import Link from "next/link";
import { useEffect, useState } from "react";

const NODES = ["Server", "Cron agent", "Secure gateway", "Event stream", "Monitoring engine", "AI analysis", "Alert router", "Dashboard"];
const FEED: [string, string, string][] = [
  ["success", "nightly-database-backup", "completed in 14m 02s"], ["success", "billing-reconciliation", "completed in 3m 41s"],
  ["late", "customer-report-generator", "delayed 6 min — inside grace"], ["failed", "database-cleanup", "exit 2 · connection timeout"],
  ["missed", "payments/settle-eod (CronJob)", "missed schedule — node pressure"], ["success", "redis-snapshot", "completed in 18s"],
  ["success", "market-data-import", "recovered after 2 failures"],
];
const PLANS: [string, string, string[]][] = [
  ["Free", "$0", ["5 jobs", "7-day history", "Email alerts"]], ["Developer", "$19/mo", ["50 jobs", "30-day history", "Slack & webhooks"]],
  ["Team", "$79/mo", ["500 jobs", "90-day history", "Teams, Discord & Telegram", "AI copilot"]], ["Business", "$299/mo", ["5,000 jobs", "1-year history", "Everything in Team"]],
  ["Enterprise", "Custom", ["Unlimited jobs", "Private deployment", "Custom retention", "Priority support"]],
];
const FAQ: [string, string][] = [
  ["Does the agent change my crontabs?", "No. Discovery is read-only. You choose per job whether to wrap it with cs-run for exit codes and logs."],
  ["What if the server loses network?", "The agent buffers events on disk and replays them in order. The server de-duplicates, so nothing is counted twice."],
  ["Can I use it without an agent?", "Yes. Every job gets a heartbeat URL. One curl at the end of a script is enough to detect failures and missed runs."],
  ["Where does my data live?", "In your own deployment if you self-host, or in an isolated tenant on ours. Environment variable values are never collected."],
  ["Does the AI change anything on my systems?", "Never. The copilot reads telemetry and suggests commands. Remediation is always a human action."],
];

const NODE_ICONS = ["🖥", "⚙", "🔒", "〰", "◎", "✦", "⇶", "▦"];
function Pipeline() {
  // 8 stages in two rows of four; packets travel the connectors via CSS keyframes (see globals.css .pkt)
  const rows = [NODES.slice(0, 4), NODES.slice(4)];
  return (
    <div className="pipe relative rounded-2xl border border-line bg-white/70 p-5 backdrop-blur dark:bg-panel/60" aria-label="Event flow from server to dashboard">
      {rows.map((row, r) => (
        <div key={r} className={`relative grid grid-cols-4 gap-3 ${r === 1 ? "mt-8" : ""}`}>
          <div className={`pipe-line absolute left-[12%] right-[12%] top-1/2 h-px bg-line ${r === 1 ? "pipe-rev" : ""}`}>
            <i className="pkt pkt-ok max-md:hidden" /><i className="pkt pkt-bad max-md:hidden" /><i className="pkt pkt-warn max-md:hidden" />
          </div>
          {row.map((n, i) => (
            <div key={n} className="node relative z-0 flex flex-col items-center gap-1 rounded-xl border border-line bg-panel px-2 py-3 text-center shadow-[0_1px_0_rgb(var(--line)),0_8px_20px_-12px_rgb(var(--ink)/.25)]">
              <span className="text-lg leading-none" aria-hidden>{NODE_ICONS[r * 4 + i]}</span><span className="text-xs font-medium">{n}</span>
            </div>))}
        </div>))}
      <svg className="pointer-events-none absolute right-[12%] top-[46%] h-[22%] w-6" viewBox="0 0 24 60" aria-hidden><path d="M12 0 v60" stroke="rgb(var(--line))" /></svg>
      <p className="mt-4 text-center text-xs text-mute">Every run becomes an event · green = success · red = failure · amber = late</p>
    </div>
  );
}

function LiveFeed() {
  const [i, setI] = useState(0);
  useEffect(() => { const t = setInterval(() => setI((x) => x + 1), 2200); return () => clearInterval(t); }, []);
  const rows = Array.from({ length: 5 }, (_, k) => FEED[(i + k) % FEED.length]);
  return (
    <ul className="rounded-2xl border border-line bg-white/60 p-2 backdrop-blur dark:bg-panel/60" aria-live="polite">
      {rows.map(([s, n, d], k) => (
        <li key={`${n}-${i}-${k}`} className="flex items-center gap-3 px-3 py-2 text-sm"><span className={`dot dot-${s}`} /><span className="font-medium">{n}</span><span className="ml-auto text-mute">{d}</span></li>))}
    </ul>
  );
}

const FEATURES: [string, string, string][] = [
  ["Schedules that expect", "A slot for every run before it happens. Silence becomes late, then missed — decided by the schedule, not by someone noticing.", "schedule"],
  ["Alerts that mean it", "Rules over job state, routed to Slack, Teams, Discord, Telegram, email or a webhook. Repeat intervals, maintenance windows, and never a page for an outage on our side.", "alerting"],
  ["Incidents, not noise", "Related failures are correlated into one thread with acknowledge, resolve, notes, and MTTD/MTTR you can stand behind.", "incidents"],
  ["Output attached", "Wrap a job with cs-run and its stdout, stderr and exit code arrive with the execution. Metered per organisation, never scanned to bill you.", "logs"],
  ["Signals to your systems", "Every state change is a signed webhook or a NATS message on your own broker. Failing destinations back off and say why.", "signals"],
  ["Isolation the database enforces", "Postgres row-level security on every tenant table, forced for every role. Self-host the same code with compose or Helm.", "security"],
];
const HOW: [string, string][] = [
  ["Connect", "Install the agent, add the Helm chart, or put one heartbeat URL at the end of any script."],
  ["Discover", "Crontabs, systemd timers and CronJobs are found read-only and appear as jobs with their schedules."],
  ["Expect", "Every schedule becomes expected runs — with grace and a deadline — so a run that never starts is still seen."],
  ["Act", "Late, missed, failed and recovered each get a state, an alert if you want one, and an incident if it keeps happening."],
];
const NATIVE = ["Linux cron", "systemd timers", "Kubernetes CronJobs"];
const VIA_HEARTBEAT = ["Jenkins", "GitHub Actions", "GitLab CI", "Airflow", "Dagster", "pg_cron", "Backups", "ETL pipelines", "Shell & Python scripts", "Any scheduler"];

function DashboardMock() {
  return (
    <div className="rounded-2xl border border-line bg-panel shadow-[0_1px_0_rgb(var(--line)),0_24px_60px_-24px_rgb(var(--accent)/.35)]">
      <div className="flex items-center gap-1.5 border-b border-line px-4 py-2.5 text-xs text-mute"><i className="h-2.5 w-2.5 rounded-full bg-bad/60" /><i className="h-2.5 w-2.5 rounded-full bg-warn/60" /><i className="h-2.5 w-2.5 rounded-full bg-ok/60" /><span className="ml-3 font-mono">jobwatch · overview</span></div>
      <div className="grid grid-cols-3 gap-3 p-4">{[["99.6%", "success today", "text-ok"], ["1.4m", "time to detect", ""], ["2", "open incidents", "text-warn"]].map(([v, l, c]) => (
        <div key={l} className="rounded-xl bg-bg p-3"><div className={`text-2xl font-semibold tabular-nums ${c}`}>{v}</div><div className="text-xs text-mute">{l}</div></div>))}</div>
      <div className="px-2 pb-2"><LiveFeed /></div>
    </div>
  );
}

export default function Welcome() {
  return (
    <main className="bg-bg text-ink">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link href="/welcome" className="flex items-center gap-2 whitespace-nowrap font-semibold tracking-tight"><span className="dot dot-healthy" />WeCrew JobWatch</Link>
          <nav className="flex items-center gap-5 whitespace-nowrap text-sm"><Link href="/product" className="hidden text-mute hover:text-ink sm:inline">Product</Link><a href="#how" className="hidden text-mute hover:text-ink sm:inline">How it works</a><a href="#pricing" className="hidden text-mute hover:text-ink sm:inline">Pricing</a><Link href="/tools/cron" className="hidden text-mute hover:text-ink md:inline">Cron checker</Link><Link href="/login" className="hidden text-mute hover:text-ink sm:inline">Sign in</Link><Link href="/login" className="btn btn-primary">Start free</Link></nav>
        </div>
      </header>

      <section className="relative overflow-hidden bg-[radial-gradient(70%_60%_at_70%_0%,rgb(var(--accent)/.10),transparent)]">
        <div className="mx-auto grid max-w-6xl items-center gap-12 px-6 pb-20 pt-16 lg:grid-cols-[1.05fr_1fr]">
          <div>
            <p className="text-sm font-medium text-accent">Monitoring for scheduled jobs</p>
            <h1 className="mt-3 text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">Never miss a scheduled job again.</h1>
            <p className="mt-5 max-w-xl text-lg text-mute">Cron, systemd timers, Kubernetes CronJobs, backups and pipelines — expected before they run, alerted when they don't, explained when they fail.</p>
            <div className="mt-8 flex flex-wrap gap-3"><Link href="/login" className="btn btn-primary px-5 py-2.5 text-base">Start monitoring</Link><Link href="/product" className="btn px-5 py-2.5 text-base">See the product</Link></div>
            <p className="mt-4 text-sm text-mute">Free for 5 jobs · self-host with compose or Helm</p>
          </div>
          <DashboardMock />
        </div>
      </section>

      <section className="border-y border-line bg-panel/50"><div className="mx-auto grid max-w-6xl grid-cols-2 gap-6 px-6 py-8 text-sm sm:grid-cols-4">
        {[["3 schedulers", "discovered read-only: cron, systemd, Kubernetes CronJobs"], ["Row-level security", "per tenant, enforced by Postgres for every role"], ["Open API", "typed spec; the dashboard uses the same one"], ["Your stack", "self-host the same code, no third-party callouts"]].map(([v, l]) => (
          <div key={v}><div className="font-semibold">{v}</div><div className="text-mute">{l}</div></div>))}
      </div></section>

      <section className="mx-auto max-w-6xl px-6 py-20">
        <h2 className="text-3xl font-semibold tracking-tight">Everything a scheduled job needs, in one place</h2>
        <p className="mt-2 max-w-2xl text-mute">Each of these is a page in the product, not a promise.</p>
        <div className="mt-8 grid gap-5 md:grid-cols-2 lg:grid-cols-3">{FEATURES.map(([t, d, id]) => (
          <Link key={id} href={`/product#${id}`} className="group rounded-2xl border border-line bg-panel p-6 transition hover:border-accent/60 hover:shadow-[0_12px_30px_-18px_rgb(var(--accent)/.5)]">
            <h3 className="font-medium">{t}</h3><p className="mt-2 text-sm text-mute">{d}</p><span className="mt-4 inline-block text-sm text-accent opacity-0 transition group-hover:opacity-100">Learn more →</span></Link>))}</div>
      </section>

      <section id="how" className="scroll-mt-16 border-t border-line bg-panel/50"><div className="mx-auto max-w-6xl px-6 py-20">
        <h2 className="text-3xl font-semibold tracking-tight">How it works</h2>
        <div className="mt-8 grid items-start gap-10 lg:grid-cols-[2fr_3fr]">
          <ol className="space-y-5">{HOW.map(([t, d], i) => (
            <li key={t} className="flex gap-4"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-panel font-mono text-sm">{i + 1}</span><div><h3 className="font-medium">{t}</h3><p className="mt-1 text-sm text-mute">{d}</p></div></li>))}</ol>
          <Pipeline />
        </div>
      </div></section>

      <section className="mx-auto max-w-6xl px-6 py-20">
        <h2 className="text-3xl font-semibold tracking-tight">Every scheduler you already run</h2>
        <div className="mt-8 grid gap-8 md:grid-cols-2">
          <div><h3 className="text-sm font-medium text-mute">Discovered by the agent</h3><ul className="mt-3 flex flex-wrap gap-2">{NATIVE.map((p) => <li key={p} className="rounded-full border border-accent/40 bg-accent/5 px-3 py-1 text-sm">{p}</li>)}</ul></div>
          <div><h3 className="text-sm font-medium text-mute">Anything else, with one heartbeat call</h3><ul className="mt-3 flex flex-wrap gap-2">{VIA_HEARTBEAT.map((p) => <li key={p} className="rounded-full border border-line bg-panel px-3 py-1 text-sm">{p}</li>)}</ul>
            <pre className="mt-4 overflow-x-auto rounded-xl border border-line bg-panel p-3 font-mono text-xs text-mute">curl -fsS "$JOBWATCH_URL/ping/$TOKEN"   # at the end of any script</pre></div>
        </div>
      </section>

      <section className="border-t border-line bg-panel/50"><div className="mx-auto grid max-w-6xl gap-10 px-6 py-20 md:grid-cols-2">
        <div><h2 className="text-2xl font-semibold tracking-tight">Alerts where your team lives</h2><p className="mt-2 text-mute">Slack, Microsoft Teams, Discord, Telegram, email, and a signed webhook for anything that accepts JSON. Every send is written to a ledger you can read back, and a platform outage on our side never pages you.</p></div>
        <div><h2 className="text-2xl font-semibold tracking-tight">Built for teams who get audited</h2><p className="mt-2 text-mute">Row-level tenant isolation enforced by Postgres, roles from viewer to owner, secrets envelope-encrypted at rest, an SSRF guard on every outbound request, self-service export and deletion of your organisation's data — and a copilot that reads but never runs anything.</p></div>
      </div></section>

      <section id="pricing" className="mx-auto max-w-6xl scroll-mt-16 px-6 py-20">
        <h2 className="text-3xl font-semibold tracking-tight">Pricing</h2>
        <div className="mt-8 grid gap-4 md:grid-cols-5">{PLANS.map(([n, p, f], i) => (
          <div key={n} className={`rounded-2xl border p-5 ${i === 2 ? "border-accent shadow-[0_12px_30px_-18px_rgb(var(--accent)/.5)]" : "border-line"}`}><h3 className="font-medium">{n}</h3><p className="mt-1 text-2xl font-semibold">{p}</p>
            <ul className="mt-3 space-y-1 text-sm text-mute">{f.map((x) => <li key={x}>{x}</li>)}</ul><Link href="/login" className={`btn mt-4 w-full justify-center ${i === 2 ? "btn-primary" : ""}`}>{i === 4 ? "Talk to us" : "Start"}</Link></div>))}</div>
        <p className="mt-3 text-sm text-mute">14-day trial of Team on every new workspace. Prices are placeholders until billing goes live.</p>
      </section>

      <section className="border-t border-line bg-panel/50"><div className="mx-auto max-w-3xl px-6 py-20">
        <h2 id="faq" className="scroll-mt-16 text-3xl font-semibold tracking-tight">Questions</h2>
        <dl className="mt-6 divide-y divide-line">{FAQ.map(([q, a]) => <div key={q} className="py-4"><dt className="font-medium">{q}</dt><dd className="mt-1 text-mute">{a}</dd></div>)}</dl>
      </div></section>

      <section className="mx-auto max-w-6xl px-6 py-24 text-center">
        <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">Know before your users do.</h2>
        <p className="mt-3 text-mute">Five jobs free. One curl to the first alert.</p>
        <div className="mt-6 flex justify-center gap-3"><Link href="/login" className="btn btn-primary px-6 py-3 text-base">Start monitoring</Link><Link href="/product" className="btn px-6 py-3 text-base">See the product</Link></div>
      </section>

      <footer className="border-t border-line"><div className="mx-auto grid max-w-6xl gap-8 px-6 py-10 text-sm sm:grid-cols-4">
        <div><div className="flex items-center gap-2 font-semibold"><span className="dot dot-healthy" />WeCrew JobWatch</div><p className="mt-2 text-xs text-mute">Monitoring for scheduled jobs. Self-hosted or hosted.</p></div>
        {[["Product", [["/product", "Feature tour"], ["/product#security", "Security"], ["/welcome#pricing", "Pricing"], ["/status/demo", "Status page example"]]],
          ["Tools", [["/tools/cron", "Cron expression checker"], ["/login", "Sign in"]]],
          ["Company", [["/welcome#faq", "FAQ"], ["/product#api", "API"]]]].map(([h, links]) => (
          <div key={h as string}><div className="font-medium">{h as string}</div><ul className="mt-2 space-y-1 text-mute">{(links as [string, string][]).map(([href, l]) => <li key={href + l}><Link href={href} className="hover:text-ink">{l}</Link></li>)}</ul></div>))}
      </div></footer>
    </main>
  );
}
