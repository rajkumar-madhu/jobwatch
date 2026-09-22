import Link from "next/link";
import { Logo } from "@/components/brand";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-[70vh] max-w-lg flex-col items-center justify-center px-6 text-center">
      <Logo />
      <h1 className="mt-8 text-xl font-semibold text-accent">Page not found</h1>
      <p className="mt-2 text-sm text-mute">That URL is not part of JobWatch. Check the address, or go back to the desk.</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Link href="/" className="btn btn-primary">Open dashboard</Link>
        <Link href="/welcome" className="btn">Back to home</Link>
      </div>
    </main>
  );
}
