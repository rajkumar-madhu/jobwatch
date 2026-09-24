"use client";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-lg flex-col items-center justify-center px-6 text-center">
      <h1 className="text-xl font-semibold text-accent">Something went wrong</h1>
      <p className="mt-2 text-sm text-mute">{error.message || "A client-side exception occurred."}</p>
      <div className="mt-6 flex gap-2">
        <button className="btn btn-primary" onClick={() => reset()}>Try again</button>
        <a className="btn" href="/welcome">Back to home</a>
      </div>
    </main>
  );
}
