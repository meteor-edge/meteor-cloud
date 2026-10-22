import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeploymentsPage } from "@/pages/deployments/DeploymentsPage";

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ token: "test-token" }),
}));

vi.mock("@/api/organizations", () => ({
  getOrganization: vi.fn(async () => ({
    id: "org-1",
    name: "Acme",
    current_user_role: "operator",
  })),
}));

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    listDeployments: vi.fn(async () => ({
      items: [
        {
          id: "dep-1",
          organization_id: "org-1",
          release_id: "rel-1",
          release: {
            id: "rel-1",
            name: "camera-agent",
            version: "1.2.0",
            kind: "systemd",
          },
          name: "Plant A rollout",
          target: {},
          rollout: { strategy: "immediate" },
          status: "in_progress",
          provider: "native",
          provider_ref: "dep-1",
          counts: {
            pending: 2,
            updating: 0,
            succeeded: 1,
            failed: 0,
            cancelled: 0,
            queued: 0,
            skipped: 0,
            total: 3,
          },
          created_by_user_id: "user-1",
          rollback_of_deployment_id: null,
          created_at: "2026-10-16T12:00:00Z",
          updated_at: "2026-10-16T12:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })),
  };
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/organizations/org-1/deployments"]}>
        <Routes>
          <Route
            path="/organizations/:organizationId/deployments"
            element={<DeploymentsPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DeploymentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists deployments and shows Deploy for operators", async () => {
    renderPage();
    expect(await screen.findByText("Plant A rollout")).toBeInTheDocument();
    expect(screen.getByText(/1\/3 succeeded/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^deploy$/i })).toBeInTheDocument();
  });
});
