/** Build a same-origin-style API auth login URL. Rejects non-http(s) API bases. */
export function authLoginHref(next: string, loginHint?: string): string {
  const base = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (!base || !/^https?:\/\//i.test(base)) {
    throw new Error("NEXT_PUBLIC_API_URL is not configured");
  }
  const path = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  const url = new URL("auth/login", base.endsWith("/") ? base : `${base}/`);
  url.searchParams.set("next", path);
  const hint = loginHint?.trim();
  if (hint && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(hint) && hint.length <= 254) {
    url.searchParams.set("login_hint", hint);
  }
  return url.href;
}

export function contactMailto(fields: { first: string; last: string; email: string; company: string }): string {
  const subject = `JobWatch inquiry — ${fields.company.slice(0, 80)}`;
  const body = `Name: ${fields.first} ${fields.last}\nEmail: ${fields.email}\nCompany: ${fields.company}\n\nI'd like to learn more about JobWatch.`;
  const url = new URL("mailto:hello@wecrew.in");
  url.searchParams.set("subject", subject);
  url.searchParams.set("body", body);
  return url.href;
}
