import Link from "next/link";
const API = process.env.NEXT_PUBLIC_API_URL;
export default function Login() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <Link href="/welcome" className="mb-8 flex items-center gap-2 font-semibold"><span className="dot dot-healthy" />WeCrew JobWatch</Link>
        <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
        <p className="mt-1 text-sm text-mute">Email/password, Google, GitHub and Microsoft are all handled by your organisation's sign-in page.</p>
        <a href={`${API}/auth/login?next=/`} className="btn btn-primary mt-6 w-full justify-center py-2">Continue to sign in</a>
        <p className="mt-6 text-xs text-mute">Using an API key instead? <Link href="/settings" className="underline">Paste it in Settings</Link>.</p>
      </div>
    </main>
  );
}
