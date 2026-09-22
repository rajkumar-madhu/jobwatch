"use client";
import Link from "next/link";
import { useState } from "react";
import { SiteFooter, SiteHeader } from "@/components/brand";
import { contactMailto } from "@/lib/auth-nav";

const FEATURES = [
  ["Schedule watch", "Know when a cron, timer, or CronJob is late, missed, or still running."],
  ["Failure detection", "Exit codes, timeouts, and missed windows are decided by the schedule."],
  ["Incident desk", "Related failures group into one incident instead of a pile of pages."],
  ["Live logs", "Stdout and stderr sit next to the run that produced them."],
  ["Alert routing", "Slack, email, Teams, and webhooks with quiet hours and de-duplication."],
  ["Servers & agents", "Linux and Kubernetes agents, with a heartbeat URL when you cannot install one."],
];

const PLANS: [string, string, string[], "trial" | "sales"][] = [
  ["Free", "$0", ["5 jobs", "7-day history", "Email alerts"], "trial"],
  ["Team", "$79/mo", ["500 jobs", "90-day history", "Slack and Kubernetes"], "trial"],
  ["Business", "$299/mo", ["5,000 jobs", "1-year history", "SSO and analytics"], "sales"],
];

export default function Welcome() {
  const [sent, setSent] = useState(false);

  const sendContact = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const first = String(fd.get("first") ?? "").trim();
    const last = String(fd.get("last") ?? "").trim();
    const email = String(fd.get("email") ?? "").trim();
    const company = String(fd.get("company") ?? "").trim();
    setSent(true);
    window.location.assign(contactMailto({ first, last, email, company }));
  };

  return (
    <div className="bg-panel text-ink">
      <SiteHeader />

      <section className="hero-band relative overflow-hidden text-white">
        <div className="relative mx-auto max-w-4xl px-5 py-20 text-center sm:py-28">
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-white/80">WeCrew</p>
          <h1 className="mt-3 text-4xl font-semibold leading-tight tracking-tight sm:text-6xl">JobWatch</h1>
          <p className="mx-auto mt-5 max-w-2xl text-base text-white/85 sm:text-lg">See every scheduled job from one operations desk. Cron, Kubernetes CronJobs, backups, and pipelines — know when a run is late, missed, or failed.</p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link href="/signup" className="btn btn-primary px-7 py-3 text-base">Start free trial</Link>
            <Link href="/login" className="btn border-white/40 bg-white/10 px-7 py-3 text-base text-white hover:bg-white/20">Sign in</Link>
          </div>
        </div>
      </section>

      <section className="bg-bg">
        <div className="mx-auto max-w-6xl px-5 py-16">
          <h2 className="max-w-3xl text-2xl font-semibold tracking-tight text-accent sm:text-3xl">One place for the jobs your business already depends on.</h2>
          <p className="mt-4 max-w-3xl text-mute">Install an agent, add a Helm chart, or put a single heartbeat URL at the end of a script. JobWatch keeps the schedule, the last run, and the reason it failed.</p>
          <div id="features" className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map(([title, body]) => (
              <article key={title} className="border-t border-line pt-4">
                <h3 className="font-semibold text-accent">{title}</h3>
                <p className="mt-1 text-sm text-mute">{body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="about" className="bg-panel">
        <div className="mx-auto grid max-w-6xl items-center gap-10 px-5 py-16 lg:grid-cols-2">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-accent sm:text-3xl">Built for the people who get paged at 2am.</h2>
            <p className="mt-4 text-mute">A missed backup should not wait until Monday. JobWatch compares each run with its schedule, expected duration, and exit code, then opens one incident when several jobs fail together.</p>
            <ul className="mt-6 space-y-3 text-sm">
              {["Read-only discovery of crontabs, timers, and CronJobs.", "Disk buffer on the agent so a network blip does not drop events.", "The copilot explains a failure. It never restarts a job for you."].map((item) => (
                <li key={item} className="flex gap-3"><span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-cta" />{item}</li>
              ))}
            </ul>
          </div>
          <div className="overflow-hidden rounded-2xl border border-line">
            <div className="border-b border-line bg-bg px-5 py-3 text-sm font-medium text-accent">Tonight’s desk</div>
            <ul className="divide-y divide-line text-sm">
              {[["healthy", "billing-reconciliation", "On time"], ["failed", "customer-report-generator", "Exit 2 · timeout"], ["missed", "mongodb-backup", "Missed the 03:00 window"], ["running", "redis-snapshot", "Started 25s ago"]].map(([s, name, note]) => (
                <li key={name} className="flex items-center gap-3 px-5 py-3"><span className={`dot dot-${s}`} /><span className="font-medium">{name}</span><span className="ml-auto text-mute">{note}</span></li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section className="bg-bg">
        <div className="mx-auto max-w-6xl px-5 py-16">
          <h2 className="text-2xl font-semibold tracking-tight text-accent sm:text-3xl">Teams that stopped finding out the next morning</h2>
          <div className="mt-8 grid gap-8 md:grid-cols-3">
            {[["The nightly backup had been skipping for nine days. A missed run now reaches us in minutes.", "Platform lead, payments"], ["We replaced a wiki of cron entries with an inventory that discovers itself.", "SRE, commerce"], ["Runtime drift showed up two weeks before the maintenance window would have failed.", "Data engineering"]].map(([quote, who]) => (
              <blockquote key={who} className="border-t border-line pt-4">
                <p className="text-sm leading-relaxed">“{quote}”</p>
                <footer className="mt-4 text-sm font-medium text-accent">{who}</footer>
              </blockquote>
            ))}
          </div>
        </div>
      </section>

      <section id="pricing" className="bg-panel">
        <div className="mx-auto max-w-6xl px-5 py-16">
          <h2 className="text-2xl font-semibold tracking-tight text-accent sm:text-3xl">Simple pricing</h2>
          <p className="mt-2 text-mute">Every new workspace starts with a 14-day Team trial.</p>
          <div className="mt-8 grid gap-4 md:grid-cols-3">
            {PLANS.map(([name, price, items, kind], i) => (
              <div key={name} className={`rounded-2xl border border-line p-6 ${i === 1 ? "ring-2 ring-cta" : ""}`}>
                <h3 className="font-semibold text-accent">{name}</h3>
                <p className="mt-2 text-3xl font-semibold">{price}</p>
                <ul className="mt-4 space-y-2 text-sm text-mute">{items.map((item) => <li key={item}>{item}</li>)}</ul>
                {kind === "sales" ? (
                  <a href="#contact" className="btn btn-navy mt-6 w-full">Talk to us</a>
                ) : (
                  <Link href="/signup" className={`btn mt-6 w-full ${i === 1 ? "btn-primary" : "btn-navy"}`}>Start free trial</Link>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="contact" className="bg-bg">
        <div className="mx-auto grid max-w-6xl gap-10 px-5 py-16 lg:grid-cols-2">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-accent sm:text-3xl">Ready to watch the jobs that cannot slip?</h2>
            <p className="mt-4 text-mute">Create a workspace, or send a note and we’ll reply from hello@wecrew.in.</p>
          </div>
          <form className="space-y-3 rounded-2xl border border-line bg-panel p-6" onSubmit={sendContact}>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm font-medium">First name<input required className="mt-1 w-full rounded-xl border border-line px-3 py-2" name="first" autoComplete="given-name" /></label>
              <label className="text-sm font-medium">Last name<input required className="mt-1 w-full rounded-xl border border-line px-3 py-2" name="last" autoComplete="family-name" /></label>
            </div>
            <label className="block text-sm font-medium">Work email<input required type="email" className="mt-1 w-full rounded-xl border border-line px-3 py-2" name="email" autoComplete="email" /></label>
            <label className="block text-sm font-medium">Company<input required className="mt-1 w-full rounded-xl border border-line px-3 py-2" name="company" autoComplete="organization" /></label>
            <button className="btn btn-primary w-full py-2.5" type="submit">Open email draft</button>
            {sent && <p className="text-sm text-ok">Your mail app should open with a draft to hello@wecrew.in. Or continue to <Link href="/signup" className="font-medium underline">create your account</Link>.</p>}
          </form>
        </div>
      </section>

      <SiteFooter />
    </div>
  );
}
