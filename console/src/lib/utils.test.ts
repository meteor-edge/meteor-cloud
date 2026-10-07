import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration, formatRelativeTime } from "@/lib/utils";

describe("artifact byte formatting", () => {
  it.each([
    [0, "0 B"],
    [1023, "1023 B"],
    [1024, "1.0 KB"],
    [1536, "1.5 KB"],
    [1024 ** 2, "1.0 MB"],
    [1024 ** 3, "1.0 GB"],
    [1024 ** 4, "1.0 TB"],
    [1024 ** 5, "1024.0 TB"],
    [-1, "—"],
    [NaN, "—"],
    [Infinity, "—"],
  ])("formats %s bytes as %s", (value, expected) => {
    expect(formatBytes(value)).toBe(expected);
  });
});

describe("upload duration formatting", () => {
  it.each([
    [-5, "0 s"],
    [0, "0 s"],
    [1.49, "1 s"],
    [1.5, "2 s"],
    [59.5, "1 min"],
    [61, "1 min 1 s"],
    [3599, "59 min 59 s"],
    [3599.5, "1 h"],
    [3901, "1 h 5 min"],
    [7200, "2 h"],
  ])("formats %s seconds as %s", (value, expected) => {
    expect(formatDuration(value)).toBe(expected);
  });
});

describe("device relative time", () => {
  const now = Date.parse("2026-10-07T12:00:00Z");

  it.each([
    [-60, "just now"],
    [0, "just now"],
    [59, "just now"],
    [60, "1 min ago"],
    [3599, "59 min ago"],
    [3600, "1 h ago"],
    [86399, "23 h ago"],
    [86400, "1 d ago"],
    [29 * 86400, "29 d ago"],
  ])("formats an age of %s seconds as %s", (seconds, expected) => {
    expect(formatRelativeTime(new Date(now - seconds * 1000).toISOString(), now)).toBe(expected);
  });

  it.each([null, undefined, "", "invalid-date"])("handles missing or invalid time %s", (value) => {
    expect(formatRelativeTime(value, now)).toBe("—");
  });

  it("switches to a local date at thirty days", () => {
    const date = new Date(now - 30 * 86400 * 1000);
    expect(formatRelativeTime(date.toISOString(), now)).toBe(date.toLocaleDateString());
  });
});
