import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReleasesPage } from "@/pages/releases/ReleasesPage";

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ token: "test-token" }),
}));

vi.mock("@/api/organizations", () => ({
  getOrganization: vi.fn(async () => ({
    id: "org-1",
    name: "Acme",
    current_user_role: "developer",
  })),
}));

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    listReleases: vi.fn(async () => ({
      items: [
        {
          id: "rel-1",
          organization_id: "org-1",
          name: "camera-agent",
          version: "1.2.0",
          kind: "systemd",
          artifact_id: "art-1",
          description: null,
          spec: { unit_name: "camera-agent.service", start: true },
          created_by_user_id: "user-1",
          created_at: "2026-10-16T12:00:00Z",
          updated_at: "2026-10-16T12:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })),
    deleteRelease: vi.fn(),
  };
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/organizations/org-1/releases"]}>
        <Routes>
          <Route path="/organizations/:organizationId/releases" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReleasesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists releases and shows create for developers", async () => {
    renderPage();
    expect(await screen.findByText("camera-agent")).toBeInTheDocument();
    expect(screen.getByText("1.2.0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create release/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^deploy$/i })).toHaveAttribute(
      "href",
      "/organizations/org-1/deployments?deploy=1&release_id=rel-1",
    );
  });
});
