import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Deployment, DeploymentDevice } from "@/api/fleet/types";
import { DeploymentDetailPage } from "@/pages/deployments/DeploymentDetailPage";

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
  return { ...actual, getDeployment: vi.fn() };
});

import { getDeployment } from "@/api/fleet";

function device(overrides: Partial<DeploymentDevice>): DeploymentDevice {
  return {
    id: "dd-1",
    deployment_id: "dep-1",
    device_id: "dev-1",
    device_name: "edge-01",
    status: "succeeded",
    progress_percent: 100,
    health: "healthy",
    reported_at: "2026-10-26T10:00:00Z",
    endpoint_availability: null,
    previous_desired_release_id: null,
    last_error: null,
    created_at: "2026-10-26T09:00:00Z",
    updated_at: "2026-10-26T10:00:00Z",
    ...overrides,
  };
}

function deployment(devices: DeploymentDevice[], status = "completed"): Deployment {
  const succeeded = devices.filter((d) => d.status === "succeeded").length;
  return {
    id: "dep-1",
    organization_id: "org-1",
    release_id: "rel-1",
    release: { id: "rel-1", name: "camera-agent", version: "1.2.0", kind: "systemd" },
    name: "Plant A rollout",
    target: {},
    rollout: {},
    status,
    provider: "native",
    provider_ref: "dep-1",
    counts: {
      pending: 0,
      queued: 0,
      updating: devices.filter((d) => d.status === "updating").length,
      succeeded,
      failed: 0,
      cancelled: 0,
      skipped: 0,
      total: devices.length,
    },
    created_by_user_id: "user-1",
    rollback_of_deployment_id: null,
    created_at: "2026-10-26T09:00:00Z",
    updated_at: "2026-10-26T10:00:00Z",
    devices,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/organizations/org-1/deployments/dep-1"]}>
        <Routes>
          <Route
            path="/organizations/:organizationId/deployments/:deploymentId"
            element={<DeploymentDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DeploymentDetailPage", () => {
  it("shows install, health and URL separately, with a legend", async () => {
    vi.mocked(getDeployment).mockResolvedValue(
      deployment([
        device({ id: "dd-1", device_name: "edge-01", endpoint_availability: "ready" }),
        device({
          id: "dd-2",
          device_id: "dev-2",
          device_name: "edge-02",
          health: "unhealthy",
          endpoint_availability: "unhealthy",
          last_error: "container app is not running (exited, exit 1)",
        }),
      ]),
    );
    renderPage();

    expect(await screen.findByText(/2\/2 installed · 1 healthy · 1 URL ready/)).toBeInTheDocument();
    const unhealthyRow = screen.getByText("edge-02").closest("tr")!;
    expect(within(unhealthyRow).getByText("Succeeded")).toBeInTheDocument();
    expect(within(unhealthyRow).getByText("Unhealthy")).toBeInTheDocument();
    expect(within(unhealthyRow).getByText("unhealthy")).toBeInTheDocument(); // URL badge
    expect(within(unhealthyRow).getByText(/exited, exit 1/)).toBeInTheDocument();
    const healthyRow = screen.getByText("edge-01").closest("tr")!;
    expect(within(healthyRow).getByText("Healthy")).toBeInTheDocument();
    expect(within(healthyRow).getByText("ready")).toBeInTheDocument();
  });

  it("shows install progress while updating, without a URL legend when no URL exists", async () => {
    vi.mocked(getDeployment).mockResolvedValue(
      deployment(
        [
          device({
            status: "updating",
            progress_percent: 40,
            health: "unknown",
            reported_at: null,
          }),
        ],
        "in_progress",
      ),
    );
    renderPage();

    expect(await screen.findByText("Updating (40%)")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.getByText(/0\/1 installed · 0 healthy/)).toBeInTheDocument();
    expect(screen.queryByText(/URL ready/)).not.toBeInTheDocument();
  });
});
