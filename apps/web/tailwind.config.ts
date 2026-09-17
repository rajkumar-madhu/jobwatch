import type { Config } from "tailwindcss";
export default {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)", panel: "rgb(var(--panel) / <alpha-value>)", line: "rgb(var(--line) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)", mute: "rgb(var(--mute) / <alpha-value>)", accent: "rgb(var(--accent) / <alpha-value>)",
        ok: "rgb(var(--ok) / <alpha-value>)", bad: "rgb(var(--bad) / <alpha-value>)", warn: "rgb(var(--warn) / <alpha-value>)", run: "rgb(var(--run) / <alpha-value>)",
      },
      fontFamily: { sans: ["IBM Plex Sans", "system-ui", "sans-serif"], mono: ["IBM Plex Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
} satisfies Config;
