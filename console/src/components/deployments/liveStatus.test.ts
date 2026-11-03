import { describe, expect, it } from "vitest";

import { LIVE_POLL_MS, listRefetchInterval } from "@/components/deployments/liveStatus";

describe("listRefetchInterval", () => {
  it("polls while any Deploy on the page is open", () => {
    expect(listRefetchInterval([{ status: "completed" }, { status: "in_progress" }])).toBe(LIVE_POLL_MS);
    expect(listRefetchInterval([{ status: "paused" }])).toBe(LIVE_POLL_MS);
  });

  it("stops polling when every Deploy is terminal or there is no data", () => {
    expect(listRefetchInterval([{ status: "completed" }, { status: "failed" }, { status: "cancelled" }])).toBe(false);
    expect(listRefetchInterval([])).toBe(false);
    expect(listRefetchInterval(undefined)).toBe(false);
  });
});
