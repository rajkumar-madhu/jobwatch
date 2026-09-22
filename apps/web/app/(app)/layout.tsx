import { AppHeader } from "@/components/app-header";
import { Sidebar } from "@/components/sidebar";
import { CommandPalette } from "@/components/command-palette";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <div className="flex min-h-screen flex-col bg-bg lg:flex-row">
        <Sidebar />
        <div className="min-w-0 flex-1">
          <AppHeader />
          <main>{children}</main>
        </div>
      </div>
      <CommandPalette />
    </>
  );
}
