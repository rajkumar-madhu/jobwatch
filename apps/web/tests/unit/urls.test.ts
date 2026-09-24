import { describe, expect, it } from "vitest";
import { apiUrl, ingestOrigin } from "@/lib/urls";

describe("ingestOrigin", () => {
  it("keeps a hosted origin and drops a trailing slash", () => {
    expect(ingestOrigin("https://jobwatch.wecrew.in/")).toBe("https://jobwatch.wecrew.in");
  });

  it("moves the local API port to the ingest port", () => {
    expect(ingestOrigin("http://localhost:8000")).toBe("http://localhost:8010");
    expect(ingestOrigin("http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8010");
  });

  it("does not treat a hostname that merely contains localhost as local", () => {
    expect(ingestOrigin("https://notlocalhost.example:8000")).toBe("https://notlocalhost.example:8000");
  });
});

describe("apiUrl", () => {
  it("joins a path without doubling the slash", () => {
    expect(apiUrl("/auth/logout", "https://jobwatch.wecrew.in/")).toBe("https://jobwatch.wecrew.in/auth/logout");
    expect(apiUrl("auth/session", "https://jobwatch.wecrew.in")).toBe("https://jobwatch.wecrew.in/auth/session");
  });
});
