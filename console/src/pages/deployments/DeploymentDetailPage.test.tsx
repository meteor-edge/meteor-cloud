import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

vi.mock("@/api/access", () => ({
  getMyAccess: vi.fn(async () => ({
    organizations: [{ organization_id: "org-1", permissions: ["device.logs"] }],
  })),
}));

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return { ...actual, getDeployment: vi.fn(), resumeDeployment: vi.fn(), rollbackDeployment: vi.fn() };
});

import { getDeployment, resumeDeployment, rollbackDeployment } from "@/api/fleet";
import { getOrganization } from "@/api/organizations";
import type { DeploymentRollout, DeploymentStatus } from "@/api/fleet/types";

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
    activated_at: "2026-10-26T09:00:00Z",
    device_last_seen_at: "2026-10-26T10:00:00Z",
    previous_desired_release_id: null,
    last_error: null,
    created_at: "2026-10-26T09:00:00Z",
    updated_at: "2026-10-26T10:00:00Z",
    ...overrides,
  };
}

function deployment(
  devices: DeploymentDevice[],
  status: DeploymentStatus = "completed",
  rollout: DeploymentRollout = {},
): Deployment {
  const succeeded = devices.filter((d) => d.status === "succeeded").length;
  return {
    id: "dep-1",
    organization_id: "org-1",
    release_id: "rel-1",
    release: { id: "rel-1", name: "camera-agent", version: "1.2.0", kind: "systemd" },
    name: "Plant A rollout",
    target: {},
    rollout,
    status,
    provider: "native",
    provider_ref: "dep-1",
    counts: {
      pending: 0,
      queued: 0,
      updating: devices.filter((d) => d.status === "updating").length,
      succeeded,
      failed: devices.filter((d) => d.status === "failed").length,
      cancelled: 0,
      skipped: 0,
      timed_out: devices.filter((d) => d.status === "timed_out").length,
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

    expect(await screen.findByText("Installing (40%)")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.getByText(/0\/1 installed · 0 healthy/)).toBeInTheDocument();
    expect(screen.queryByText(/URL ready/)).not.toBeInTheDocument();
  });

  it("shows a paused Deploy with its reason, phase and a Resume button", async () => {
    vi.mocked(getDeployment).mockResolvedValue(
      deployment(
        [
          device({ id: "dd-1", status: "failed", health: "unhealthy" }),
          device({ id: "dd-2", device_id: "dev-2", device_name: "edge-02", status: "queued", activated_at: null }),
        ],
        "paused",
        {
          strategy: "phased",
          phases: [{ count: 1 }, { percent: 100, delay_seconds: 3600 }],
          state: { current_phase: 0, paused_reason: "failure_threshold", paused_at: "2026-10-26T10:00:00Z" },
        },
      ),
    );
    vi.mocked(resumeDeployment).mockResolvedValue({} as Deployment);
    renderPage();

    expect(await screen.findByText(/Paused \(failure threshold reached\)/)).toBeInTheDocument();
    expect(screen.getByText("Phase 1 of 2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(resumeDeployment).toHaveBeenCalledWith("test-token", "org-1", "dep-1");
  });

  it("shows Pause and the soak time left while a phased Deploy waits", async () => {
    const completed = new Date(Date.now() - 20 * 60 * 1000).toISOString();
    vi.mocked(getDeployment).mockResolvedValue(
      deployment(
        [
          device({ id: "dd-1" }),
          device({ id: "dd-2", device_id: "dev-2", device_name: "edge-02", status: "queued", activated_at: null }),
        ],
        "in_progress",
        {
          strategy: "phased",
          phases: [{ count: 1 }, { percent: 100, delay_seconds: 3600 }],
          state: { current_phase: 0, phase_completed_at: completed },
        },
      ),
    );
    renderPage();

    expect(await screen.findByText("Phase 1 of 2 · next phase in 40 min")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  });

  it("counts the soak time down even when polls return the same data", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const completed = new Date(Date.now() - 20 * 60 * 1000).toISOString();
      vi.mocked(getDeployment).mockResolvedValue(
        deployment(
          [
            device({ id: "dd-1" }),
            device({ id: "dd-2", device_id: "dev-2", device_name: "edge-02", status: "queued", activated_at: null }),
          ],
          "in_progress",
          {
            strategy: "phased",
            phases: [{ count: 1 }, { percent: 100, delay_seconds: 3600 }],
            state: { current_phase: 0, phase_completed_at: completed },
          },
        ),
      );
      renderPage();
      expect(await screen.findByText("Phase 1 of 2 · next phase in 40 min")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
      });
      expect(screen.getByText("Phase 1 of 2 · next phase in 30 min")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("labels devices that have not checked in since activation, and timed out devices", async () => {
    vi.mocked(getDeployment).mockResolvedValue(
      deployment(
        [
          device({
            id: "dd-1",
            device_name: "edge-01",
            status: "pending",
            health: null,
            activated_at: "2026-10-26T10:00:00Z",
            device_last_seen_at: "2026-10-26T09:00:00Z",
          }),
          device({
            id: "dd-2",
            device_id: "dev-2",
            device_name: "edge-02",
            status: "pending",
            health: null,
            device_last_seen_at: null,
          }),
          device({
            id: "dd-3",
            device_id: "dev-3",
            device_name: "edge-03",
            status: "pending",
            health: null,
            activated_at: "2026-10-26T09:00:00Z",
            device_last_seen_at: "2026-10-26T09:30:00Z",
          }),
          device({ id: "dd-4", device_id: "dev-4", device_name: "edge-04", status: "timed_out", health: null }),
        ],
        "in_progress",
      ),
    );
    renderPage();

    const waiting = (await screen.findByText("edge-01")).closest("tr")!;
    expect(within(waiting).getByText("Waiting for device")).toBeInTheDocument();
    const neverSeen = screen.getByText("edge-02").closest("tr")!;
    expect(within(neverSeen).getByText("Waiting for device")).toBeInTheDocument();
    const checkedIn = screen.getByText("edge-03").closest("tr")!;
    expect(within(checkedIn).getByText("Pending")).toBeInTheDocument();
    const timedOut = screen.getByText("edge-04").closest("tr")!;
    expect(within(timedOut).getByText("Timed out")).toBeInTheDocument();
    expect(screen.getByText(/1 timed out/)).toBeInTheDocument();
  });

  it("links each device row to that device's logs", async () => {
    vi.mocked(getDeployment).mockResolvedValue(deployment([device({ status: "failed" })], "failed"));
    renderPage();

    const link = await screen.findByRole("link", { name: "Logs for edge-01" });
    expect(link).toHaveAttribute("href", "/organizations/org-1/devices/dev-1?tab=logs");
  });

  it("rolls back per previous release and says which devices were left alone", async () => {
    vi.mocked(getOrganization).mockResolvedValue({
      id: "org-1",
      name: "Acme",
      current_user_role: "owner",
    } as never);
    vi.mocked(getDeployment).mockImplementation(async (_token, _org, id) => ({
      ...deployment([device({ status: "failed" })], "failed"),
      id,
    }));
    vi.mocked(rollbackDeployment).mockResolvedValue({
      deployments: [
        { ...deployment([device({})]), id: "dep-2" },
        {
          ...deployment([device({})]),
          id: "dep-3",
          release: { id: "rel-0", name: "camera-agent", version: "1.0.0", kind: "systemd" },
        },
      ],
      skipped: [{ device_id: "dev-9", device_name: "edge-new", reason: "first install" }],
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Rollback" }));
    expect(await screen.findByText(/rollback started/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "camera-agent 1.0.0" })).toHaveAttribute(
      "href",
      "/organizations/org-1/deployments/dep-3",
    );
    expect(screen.getByText(/left as is.*edge-new/i)).toBeInTheDocument();
  });
});
