import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "@/components/providers";
export const metadata: Metadata = { title: "CronSentinel — never miss a scheduled job", description: "Monitor cron jobs, Kubernetes CronJobs, backups, pipelines and scripts from one platform." };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (<html lang="en" suppressHydrationWarning><body><Providers>{children}</Providers></body></html>);
}
