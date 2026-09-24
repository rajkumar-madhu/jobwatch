"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, mutate } from "@/lib/api";
import { ago } from "@/lib/format";
import { Page, Skeleton, ErrorBox } from "@/components/ui";

// R35: plan_limits.features keys → what the customer reads. Keys not listed render raw, which is a
// signal to add a label (or to ask whether the feature exists at all — see migration 0015).
const FEATURE_LABEL: Record<string, string> = { slack: "Slack", webhook: "Webhooks", teams: "Microsoft Teams", discord: "Discord",
  telegram: "Telegram", ai: "AI copilot", kubernetes: "Kubernetes agent" };

export default function BillingPage() {
  const q = useQuery({ queryKey: ["billing"], queryFn: () => api<any>("/api/v1/billing") });
  const checkout = useMutation({ mutationFn: (plan: string) => mutate("/api/v1/billing/checkout", "post", { query: { plan } }) as Promise<{ url: string }>, onSuccess: (d) => (location.href = d.url) });
  const portal = useMutation({ mutationFn: () => mutate("/api/v1/billing/portal", "post", {}) as Promise<{ url: string }>, onSuccess: (d) => (location.href = d.url) });
  if (q.error) return <Page title="Billing"><ErrorBox error={q.error} /></Page>;
  if (!q.data) return <Page title="Billing"><Skeleton /></Page>;
  const { subscription: s, plans, usage, limits, configured } = q.data;
  const pct = limits?.max_jobs ? Math.min(100, Math.round(100 * usage.jobs / limits.max_jobs)) : 0;
  return (
    <Page title="Billing">
      {(checkout.error || portal.error) && <div className="mb-3"><ErrorBox error={checkout.error || portal.error} /></div>}
      <div className="mb-8 grid gap-6 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-panel p-4 text-sm"><h2 className="font-medium">Current plan</h2>
          <p className="mt-1 text-2xl font-semibold capitalize">{s?.effective_plan ?? "free"}</p>
          <p className="text-mute">{s?.status}{s?.trial_ends_at && new Date(s.trial_ends_at) > new Date() && <> · trial ends {ago(s.trial_ends_at)}</>}{s?.current_period_end && <> · renews {ago(s.current_period_end)}</>}{s?.cancel_at_period_end && <span className="text-warn"> · cancels at period end</span>}</p>
          {s?.has_customer && <button className="btn mt-3" onClick={() => portal.mutate()}>Manage payment & invoices</button>}</div>
        <div className="rounded-lg border border-line bg-panel p-4 text-sm"><h2 className="font-medium">Usage this month</h2>
          <p className="mt-1">{usage.jobs} of {limits?.max_jobs ?? "∞"} jobs</p><div className="mt-1 h-1.5 rounded bg-line"><div className={`h-full rounded ${pct >= 90 ? "bg-bad" : "bg-accent"}`} style={{ width: `${pct}%` }} /></div>
          <p className="mt-2 text-mute">{usage.executions_this_month} executions · {(usage.log_bytes / 1e6).toFixed(1)} MB logs · {limits?.retention_days ?? "custom"}-day retention</p></div>
      </div>
      {!configured && <p className="mb-4 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-sm">Stripe is not configured on this deployment — plan changes are disabled. Set <code className="font-mono">STRIPE_SECRET_KEY</code> and price IDs in <code className="font-mono">plan_limits</code>.</p>}
      <div className="grid gap-3 md:grid-cols-5">{plans.map((p: any) => { const cur = p.plan === (s?.effective_plan ?? "free"); return (
        <div key={p.plan} className={`rounded-lg border p-4 text-sm ${cur ? "border-accent" : "border-line"}`}><h3 className="font-medium capitalize">{p.plan}</h3><p className="text-xl font-semibold">{p.monthly_usd == null ? "Custom" : p.monthly_usd === 0 ? "$0" : `$${p.monthly_usd}/mo`}</p>
          <ul className="mt-2 space-y-0.5 text-mute"><li>{p.max_jobs ?? "Unlimited"} jobs</li><li>{p.retention_days ?? "Custom"}{p.retention_days ? "-day" : ""} history</li>{p.features.filter((f: string) => !["email", "all"].includes(f)).map((f: string) => <li key={f}>{FEATURE_LABEL[f] ?? f}</li>)}</ul>
          {cur ? <span className="mt-3 block text-xs text-accent">Current</span> : p.plan === "enterprise" ? <a className="btn mt-3 w-full justify-center" href="mailto:hello@wecrew.in">Contact us</a> : <button className="btn btn-primary mt-3 w-full justify-center" disabled={!configured || checkout.isPending} onClick={() => checkout.mutate(p.plan)}>{plans.findIndex((x: any) => x.plan === p.plan) > plans.findIndex((x: any) => x.plan === (s?.effective_plan ?? "free")) ? "Upgrade" : "Downgrade"}</button>}</div>); })}</div>
    </Page>
  );
}
