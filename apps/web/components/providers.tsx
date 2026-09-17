"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchInterval: 15_000, staleTime: 5_000, retry: 1 } } }));
  useEffect(() => { if (localStorage.getItem("cs_theme") === "dark") document.documentElement.classList.add("dark"); }, []);
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
