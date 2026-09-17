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
const PLATFORMS = ["Linux cron", "systemd timers", "Kubernetes CronJobs", "Docker", "Jenkins", "GitHub Actions", "GitLab CI", "Airflow", "Dagster", "pg_cron", "Backups", "ETL pipelines", "Shell & Python scripts", "Webhooks", "Any custom scheduler"];
const PLANS: [string, string, string[]][] = [
  ["Free", "$0", ["5 jobs", "7-day history", "Email alerts"]], ["Developer", "$19/mo", ["50 jobs", "30-day history", "Slack & webhooks"]],
  ["Team", "$79/mo", ["500 jobs", "90-day history", "AI diagnostics", "Kubernetes"]], ["Business", "$299/mo", ["5,000 jobs", "1-year history", "Advanced analytics", "SSO"]],
  ["Enterprise", "Custom", ["Unlimited jobs", "SAML", "Private deployment", "Custom retention", "Priority support"]],
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
            <i className="pkt pkt-ok" /><i className="pkt pkt-bad" /><i className="pkt pkt-warn" />
          </div>
          {row.map((n, i) => (
            <div key={n} className="node relative z-10 flex flex-col items-center gap-1 rounded-xl border border-line bg-panel px-2 py-3 text-center shadow-[0_1px_0_rgb(var(--line)),0_8px_20px_-12px_rgb(var(--ink)/.25)]">
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

export default function Welcome() {
  return (
    <main className="bg-[radial-gradient(60%_50%_at_50%_0%,rgb(var(--accent)/.08),transparent)]">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <span className="flex items-center gap-2 font-semibold"><span className="dot dot-healthy" />WeCrew JobWatch</span>
        <nav className="flex items-center gap-5 text-sm"><a href="#how" className="hidden sm:inline">How it works</a><a href="#pricing" className="hidden sm:inline">Pricing</a><Link href="/tools/cron" className="hidden sm:inline">Cron checker</Link><Link href="/login" className="btn">Sign in</Link></nav>
      </header>

      <section className="mx-auto max-w-6xl px-6 pb-16 pt-14">
        <h1 className="max-w-3xl text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">Never miss a scheduled job again.</h1>
        <p className="mt-5 max-w-2xl text-lg text-mute">Monitor cron jobs, Kubernetes CronJobs, backups, pipelines, scripts and scheduled infrastructure from one intelligent operations platform.</p>
        <div className="mt-8 flex gap-3"><Link href="/login" className="btn btn-primary px-5 py-2.5 text-base">Start monitoring</Link><Link href="/" className="btn px-5 py-2.5 text-base">View live demo</Link></div>
        <div className="mt-12 grid gap-4 lg:grid-cols-[3fr_2fr]"><Pipeline /><LiveFeed /></div>
        <dl className="mt-8 grid grid-cols-2 gap-6 text-sm sm:grid-cols-4">{[["12,842", "jobs monitored"], ["99.98%", "successful executions"], ["43", "incidents prevented today"], ["2.3M", "executions analysed"]].map(([v, l]) => (
          <div key={l}><dt className="text-2xl font-semibold tabular-nums">{v}</dt><dd className="text-mute">{l}</dd></div>))}</dl>
      </section>

      <section id="how" className="border-t border-line bg-panel/60"><div className="mx-auto max-w-6xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight">How it works</h2>
        <ol className="mt-6 grid gap-6 md:grid-cols-4">{[["Connect", "Install the agent, Helm chart, or add a heartbeat URL to any script."], ["Discover", "Crontabs, timers and CronJobs are found and named automatically."], ["Watch", "Every run is checked against its schedule, expected duration and exit code."], ["Understand", "Failures are correlated with host metrics, deploys and each other, then explained."]].map(([t, d], i) => (
          <li key={t} className="rounded-xl border border-line bg-panel p-5"><span className="font-mono text-xs text-mute">{i + 1}</span><h3 className="mt-2 font-medium">{t}</h3><p className="mt-1 text-sm text-mute">{d}</p></li>))}</ol>
      </div></section>

      <section className="mx-auto max-w-6xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight">Every scheduler you already run</h2>
        <ul className="mt-6 flex flex-wrap gap-2">{PLATFORMS.map((p) => <li key={p} className="rounded-full border border-line bg-panel px-3 py-1 text-sm">{p}</li>)}</ul>
      </section>

      <section className="border-t border-line bg-panel/60"><div className="mx-auto grid max-w-6xl gap-10 px-6 py-16 md:grid-cols-3">
        {[["Real-time monitoring", "Did it run, on time, and finish? Late, missed, failed and timed-out states are decided by the schedule — not by whether someone noticed."],
          ["AI incident analysis", "“Why did backup-prod fail?” gets an answer with evidence: the twelve PostgreSQL timeouts, the 340% latency jump, the three sibling jobs that failed in the same minute."],
          ["Infrastructure health", "Runtime that grew 320% while disk I/O latency went from 12 ms to 188 ms is one sentence on the job page, not a spreadsheet exercise."]].map(([t, d]) => (
          <div key={t}><h3 className="text-lg font-medium">{t}</h3><p className="mt-2 text-mute">{d}</p></div>))}
      </div></section>

      <section className="mx-auto max-w-6xl px-6 py-16">
        <div className="grid gap-10 md:grid-cols-2">
          <div><h2 className="text-2xl font-semibold tracking-tight">Alerts where your team lives</h2><p className="mt-2 text-mute">Slack, Microsoft Teams, Discord, Telegram, email, PagerDuty, Opsgenie, SMS and signed webhooks — with business hours, maintenance windows, escalation and de-duplication so one outage is one page, not forty.</p></div>
          <div><h2 className="text-2xl font-semibold tracking-tight">Built for regulated teams</h2><p className="mt-2 text-mute">Row-level tenant isolation, role-based access, argon2-hashed keys, encrypted channel secrets, TLS everywhere, full audit log. Agents never read environment variable values. Self-host the whole stack if you need to.</p></div>
        </div>
      </section>

      <section className="border-t border-line bg-panel/60"><div className="mx-auto max-w-6xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight">From teams who stopped finding out on Monday</h2>
        <div className="mt-6 grid gap-6 md:grid-cols-3">{[["“The nightly backup had been silently skipping for nine days. Now a missed run pages us in five minutes.”", "Platform lead, fintech"], ["“We replaced a wiki page of cron entries nobody trusted with a live inventory that discovers itself.”", "SRE, e-commerce"], ["“The runtime-drift alert caught a table scan two weeks before it would have blown the maintenance window.”", "Data engineering manager, analytics"]].map(([q, w]) => (
          <blockquote key={w} className="rounded-xl border border-line bg-panel p-5"><p>{q}</p><footer className="mt-3 text-sm text-mute">{w}</footer></blockquote>))}</div>
        <p className="mt-3 text-xs text-mute">Illustrative quotes — replace with customer-approved references before launch.</p>
      </div></section>

      <section id="pricing" className="mx-auto max-w-6xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight">Pricing</h2>
        <div className="mt-6 grid gap-4 md:grid-cols-5">{PLANS.map(([n, p, f], i) => (
          <div key={n} className={`rounded-xl border p-5 ${i === 2 ? "border-accent" : "border-line"}`}><h3 className="font-medium">{n}</h3><p className="mt-1 text-2xl font-semibold">{p}</p>
            <ul className="mt-3 space-y-1 text-sm text-mute">{f.map((x) => <li key={x}>{x}</li>)}</ul><Link href="/login" className={`btn mt-4 w-full justify-center ${i === 2 ? "btn-primary" : ""}`}>{i === 4 ? "Contact us" : "Start free"}</Link></div>))}</div>
        <p className="mt-3 text-sm text-mute">14-day trial of Team on every new workspace. Prices are placeholders until billing goes live.</p>
      </section>

      <section className="border-t border-line bg-panel/60"><div className="mx-auto max-w-3xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight">Questions</h2>
        <dl className="mt-6 divide-y divide-line">{FAQ.map(([q, a]) => <div key={q} className="py-4"><dt className="font-medium">{q}</dt><dd className="mt-1 text-mute">{a}</dd></div>)}</dl>
      </div></section>

      <section className="mx-auto max-w-6xl px-6 py-20 text-center">
        <h2 className="text-3xl font-semibold tracking-tight">Know before your users do.</h2>
        <Link href="/login" className="btn btn-primary mt-6 px-6 py-3 text-base">Start monitoring</Link>
      </section>
      <footer className="border-t border-line px-6 py-6 text-center text-xs text-mute">WeCrew JobWatch · <Link href="/tools/cron">Cron checker</Link> · <Link href="/status/demo">Status page example</Link></footer>
    </main>
  );
}
