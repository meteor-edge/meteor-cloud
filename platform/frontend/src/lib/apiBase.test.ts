import { afterEach, describe, expect, it, vi } from "vitest";

import { resolveApiBaseUrl } from "@/lib/apiBase";

describe("resolveApiBaseUrl", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the page origin on a deployed host (Traefik or Cloud Run load balancer)", () => {
    vi.stubGlobal("window", {
      location: { hostname: "34.1.2.3", protocol: "http:", origin: "http://34.1.2.3" },
    });

    expect(resolveApiBaseUrl()).toBe("http://34.1.2.3");
  });

  it("keeps the Vite API URL on localhost", () => {
    vi.stubGlobal("window", {
      location: { hostname: "localhost", protocol: "http:", origin: "http://localhost:5173" },
    });

    expect(resolveApiBaseUrl()).toBe("http://localhost:8000");
  });
});
