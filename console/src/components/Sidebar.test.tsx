import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/Sidebar";

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ isAuthenticated: false }),
}));
vi.mock("@/context/OrganizationContext", () => ({
  useOrganizationContext: () => ({ selectedOrganization: undefined }),
}));

describe("Sidebar", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("collapses and remembers the navigation rail preference", () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    expect(localStorage.getItem("meteorcloud-sidebar-collapsed")).toBe("true");
  });
});
