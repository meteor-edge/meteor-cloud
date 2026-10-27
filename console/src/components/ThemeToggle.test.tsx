import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "@/components/ThemeToggle";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to dark even when the system prefers light", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));

    render(<ThemeToggle />);

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
  });

  it("preserves a saved light preference", () => {
    localStorage.setItem("meteorcloud-theme", "light");

    render(<ThemeToggle />);

    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("meteorcloud-theme")).toBe("light");
    expect(screen.getByRole("button", { name: "Switch to dark mode" })).toBeInTheDocument();
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
