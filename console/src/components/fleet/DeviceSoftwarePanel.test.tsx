import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeviceSoftwarePanel } from "@/components/fleet/DeviceSoftwarePanel";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    listDeviceSoftware: vi.fn(),
  };
});

import { listDeviceSoftware } from "@/api/fleet";

const listMock = vi.mocked(listDeviceSoftware);

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeviceSoftwarePanel token="t" organizationId="org-1" deviceId="dev-1" />
    </QueryClientProvider>,
  );
}

describe("DeviceSoftwarePanel", () => {
  beforeEach(() => {
    listMock.mockReset();
  });

  it("shows empty state when no software is assigned", async () => {
    listMock.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText(/no software assigned/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /deploy a release/i })).not.toBeInTheDocument();
  });

  it("shows deploy CTA when canDeploy is set", async () => {
    listMock.mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DeviceSoftwarePanel
            token="t"
            organizationId="org-1"
            deviceId="dev-1"
            canDeploy
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("link", { name: /deploy a release/i })).toHaveAttribute(
      "href",
      "/organizations/org-1/deployments?deploy=1&device_id=dev-1",
    );
  });

  it("lists desired vs installed releases", async () => {
    listMock.mockResolvedValue([
      {
        id: "sw-1",
        organization_id: "org-1",
        device_id: "dev-1",
        workload_key: "camera-agent.service",
        kind: "systemd",
        desired_release_id: "rel-1",
        desired_revision: 1,
        desired_spec: {},
        desired_release: {
          id: "rel-1",
          name: "camera-agent",
          version: "1.2.0",
          kind: "systemd",
        },
        installed_release_id: null,
        installed_version: null,
        installed_release: null,
        health: "unknown",
        sync_status: "pending",
        progress_percent: 0,
        attempt_count: 0,
        last_error: null,
        reported_at: null,
        created_at: "2026-10-16T12:00:00Z",
        updated_at: "2026-10-16T12:00:00Z",
      },
    ]);
    renderPanel();
    expect(await screen.findByText("camera-agent.service")).toBeInTheDocument();
    expect(screen.getByText("camera-agent 1.2.0")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("shows updating progress percent", async () => {
    listMock.mockResolvedValue([
      {
        id: "sw-1",
        organization_id: "org-1",
        device_id: "dev-1",
        workload_key: "camera-agent.service",
        kind: "systemd",
        desired_release_id: "rel-1",
        desired_revision: 1,
        desired_spec: {},
        desired_release: {
          id: "rel-1",
          name: "camera-agent",
          version: "1.2.0",
          kind: "systemd",
        },
        installed_release_id: null,
        installed_version: null,
        installed_release: null,
        health: "unknown",
        sync_status: "updating",
        progress_percent: 42,
        attempt_count: 1,
        last_error: null,
        reported_at: null,
        created_at: "2026-10-16T12:00:00Z",
        updated_at: "2026-10-16T12:00:00Z",
      },
    ]);
    renderPanel();
    expect(await screen.findByText("Updating (42%)")).toBeInTheDocument();
  });

  it("shows last_error on failed sync", async () => {
    listMock.mockResolvedValue([
      {
        id: "sw-1",
        organization_id: "org-1",
        device_id: "dev-1",
        workload_key: "camera-agent.service",
        kind: "systemd",
        desired_release_id: "rel-1",
        desired_revision: 1,
        desired_spec: {},
        desired_release: {
          id: "rel-1",
          name: "camera-agent",
          version: "1.2.0",
          kind: "systemd",
        },
        installed_release_id: null,
        installed_version: null,
        installed_release: null,
        health: "unhealthy",
        sync_status: "failed",
        progress_percent: 0,
        attempt_count: 1,
        last_error: "Downloaded artifact failed SHA-256 verification.",
        reported_at: null,
        created_at: "2026-10-16T12:00:00Z",
        updated_at: "2026-10-16T12:00:00Z",
      },
    ]);
    renderPanel();
    expect(await screen.findByText(/SHA-256 verification/i)).toBeInTheDocument();
  });
});
