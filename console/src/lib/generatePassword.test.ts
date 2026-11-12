import { describe, expect, it } from "vitest";

import { generatePassword } from "@/lib/generatePassword";

describe("generatePassword", () => {
  it("returns the requested length from crypto", () => {
    expect(generatePassword(20)).toHaveLength(20);
    expect(generatePassword(8)).toHaveLength(8);
  });

  it("clamps to Wi-Fi-safe maximum", () => {
    expect(generatePassword(100)).toHaveLength(63);
  });
});
