import { describe, expect, it } from "vitest";
import { dur, statusLabel, ts, ago } from "@/lib/format";

describe("dur", () => {
  it("renders sub-second runs in ms", () => expect(dur(350)).toBe("350 ms"));
  it("switches to seconds at 1s", () => expect(dur(1000)).toBe("1s"));
  it("switches to minutes past 90s", () => expect(dur(95_000)).toBe("1m 35s"));
  it("switches to hours past 90m", () => expect(dur(2 * 3600_000)).toBe("2h 0m"));
  it("renders an em dash for no duration", () => { expect(dur(null)).toBe("—"); });
  it("treats a zero-length run as 0 ms, not as missing", () => expect(dur(0)).toBe("0 ms"));
});

describe("ago / ts", () => {
  it("render an em dash for null rather than 'Invalid Date'", () => {
    expect(ago(null)).toBe("—");
    expect(ts(null)).toBe("—");
  });
  it("formats a real timestamp", () => expect(ts("2026-03-01T10:05:09Z")).toMatch(/Mar 1|Mar  1/));
  it("suffixes relative times", () => expect(ago(new Date(Date.now() - 60_000).toISOString())).toContain("ago"));
});

describe("statusLabel", () => {
  it("covers every status the API can return", () => {
    // Mirrors the job_status + exec_status enums; a new state must get a label or the UI shows a raw slug.
    for (const s of ["healthy", "running", "late", "missed", "failed", "timeout", "recovered", "paused", "unknown", "success", "scheduled"])
      expect(statusLabel[s], `no label for ${s}`).toBeTruthy();
  });
  it("does not render 'unknown' as an error state", () => expect(statusLabel.unknown).toBe("No runs yet"));
});
