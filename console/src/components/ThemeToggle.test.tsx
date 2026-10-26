import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/ThemeToggle";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("uses the saved theme and persists a toggle", async () => {
    localStorage.setItem("meteorcloud-theme", "dark");
    const user = userEvent.setup();

    render(<ThemeToggle />);
    await user.click(screen.getByRole("button", { name: "Switch to light mode" }));

    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("meteorcloud-theme")).toBe("light");
  });
});
