"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, mutate, getKey, setKey, type Job } from "@/lib/api";
import { Page, ErrorBox } from "@/components/ui";

export default function SettingsPage() {
  const qc = useQueryClient();
  const [k, setK] = useState(getKey() ?? ""); const [saved, setSaved] = useState(false);
  const sess = useQuery({ queryKey: ["session"], queryFn: () => api<any>("/auth/session"), retry: false });
  const pages = useQuery({ queryKey: ["status-pages"], queryFn: () => api<any[]>("/api/v1/status-pages") });
  const jobs = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?limit=200") });
  const [np, setNp] = useState({ slug: "", title: "", job_ids: [] as string[] });
  const create = useMutation({ mutationFn: () => mutate("/api/v1/status-pages", "post", { body: np }), onSuccess: () => { setNp({ slug: "", title: "", job_ids: [] }); qc.invalidateQueries({ queryKey: ["status-pages"] }); } });
  const del = useMutation({ mutationFn: (id: string) => mutate("/api/v1/status-pages/{page_id}", "delete", { path: { page_id: id } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["status-pages"] }) });
  const inp = "mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-sm";
  return (
    <Page title="Settings">
      <div className="max-w-2xl space-y-10 text-sm">
        <section><h2 className="mb-2 font-medium">Account</h2>
          {sess.data ? <p>Signed in as <b>{sess.data.user.email}</b>{sess.data.orgs?.length > 1 && <> · {sess.data.orgs.length} workspaces</>}</p>
            : <p className="text-mute">Not signed in with an account. <a className="underline" href={`${process.env.NEXT_PUBLIC_API_URL}/auth/login?next=/settings`}>Sign in</a> or use an API key below.</p>}</section>
        <section><h2 className="mb-2 font-medium">API key (alternative to sign-in)</h2>
          <input className={`${inp} font-mono`} value={k} onChange={(e) => { setK(e.target.value); setSaved(false); }} placeholder="cs_xxxx.…" />
          <button className="btn btn-primary mt-2" onClick={() => { setKey(k); setSaved(true); }}>Save key</button>{saved && <span className="ml-2 text-ok">Saved.</span>}
          <p className="mt-1 text-mute">Stored in this browser only.</p></section>
        <section><h2 className="mb-2 font-medium">Public status pages</h2>
          {pages.data?.map((p) => <div key={p.id} className="flex items-center justify-between border-b border-line py-2"><span>{p.title} <a className="ml-2 font-mono text-xs text-accent underline" href={`/status/${p.slug}`} target="_blank">/status/{p.slug}</a> · {p.job_ids.length} jobs</span><button className="btn text-bad" onClick={() => confirm("Delete page?") && del.mutate(p.id)}>Delete</button></div>)}
          <div className="mt-3 rounded-lg border border-line bg-panel p-4">
            <div className="grid gap-3 sm:grid-cols-2"><label>Title<input className={inp} value={np.title} onChange={(e) => setNp({ ...np, title: e.target.value })} placeholder="Acme scheduled jobs" /></label>
              <label>Slug<input className={`${inp} font-mono`} value={np.slug} onChange={(e) => setNp({ ...np, slug: e.target.value })} placeholder="acme" /></label></div>
            <div className="mt-3">Jobs to publish<div className="mt-1 flex flex-wrap gap-2">{jobs.data?.items.map((j) => <label key={j.id} className="flex items-center gap-1"><input type="checkbox" checked={np.job_ids.includes(j.id)} onChange={(e) => setNp({ ...np, job_ids: e.target.checked ? [...np.job_ids, j.id] : np.job_ids.filter((x) => x !== j.id) })} />{j.name}</label>)}</div></div>
            {create.error && <div className="mt-2"><ErrorBox error={create.error} /></div>}
            <button className="btn btn-primary mt-3" disabled={!np.slug || !np.title} onClick={() => create.mutate()}>Publish status page</button></div></section>
      </div>
    </Page>
  );
}
