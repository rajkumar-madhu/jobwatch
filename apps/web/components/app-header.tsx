"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const CTA: { match: (p: string) => boolean; href: string; label: string }[] = [
  { match: (p) => p === "/jobs" || p.startsWith("/jobs/"), href: "/jobs?new=1", label: "Add job" },
  { match: (p) => p === "/failures", href: "/jobs?status=failed", label: "View jobs" },
  { match: (p) => p.startsWith("/incidents"), href: "/incidents", label: "All incidents" },
  { match: (p) => p === "/alerting", href: "/alerting", label: "Add channel" },
  { match: (p) => p === "/agents", href: "/agents", label: "Install agent" },
  { match: (p) => p === "/copilot", href: "/copilot", label: "Ask copilot" },
  { match: () => true, href: "/jobs", label: "View jobs" },
];

export function AppHeader() {
  const path = usePathname() ?? "/";
  const action = CTA.find((c) => c.match(path))!;
  return (
    <header className="sticky top-0 z-20 hidden items-center justify-between border-b border-line bg-panel px-6 py-3 lg:flex">
      <p className="text-sm text-mute">Operations desk</p>
      <Link href={action.href} className="btn btn-primary">{action.label}</Link>
    </header>
  );
}
