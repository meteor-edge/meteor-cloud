import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DeviceSoftware } from "@/api/fleet";
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

function renderPanel(props: { deviceOnline?: boolean; logsAvailable?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeviceSoftwarePanel token="t" organizationId="org-1" deviceId="dev-1" {...props} />
    </QueryClientProvider>,
  );
}

function inSyncRow(overrides: Partial<DeviceSoftware> = {}): DeviceSoftware {
  return {
    id: "sw-1",
    organization_id: "org-1",
    device_id: "dev-1",
    workload_key: "app",
    kind: "docker",
    desired_release_id: "rel-1",
    desired_revision: 1,
    desired_spec: {},
    desired_release: { id: "rel-1", name: "app", version: "2.0", kind: "docker" },
    installed_release_id: "rel-1",
    installed_version: "2.0",
    installed_release: { id: "rel-1", name: "app", version: "2.0", kind: "docker" },
    health: "healthy",
    sync_status: "in_sync",
    progress_percent: 100,
    attempt_count: 1,
    last_error: null,
    reported_at: new Date(Date.now() - 5_000).toISOString(),
    created_at: "2026-10-16T12:00:00Z",
    updated_at: "2026-10-16T12:00:00Z",
    ...overrides,
  };
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
          <DeviceSoftwarePanel token="t" organizationId="org-1" deviceId="dev-1" canDeploy />
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

  it("shows health as a badge with when it was reported", async () => {
    listMock.mockResolvedValue([inSyncRow()]);
    renderPanel();
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("In sync")).toBeInTheDocument();
    expect(screen.getByText("reported just now")).toBeInTheDocument();
  });

  it("marks health stale when the device is offline", async () => {
    listMock.mockResolvedValue([inSyncRow()]);
    renderPanel({ deviceOnline: false });
    expect(await screen.findByText("Stale")).toBeInTheDocument();
    expect(screen.queryByText("Healthy")).not.toBeInTheDocument();
  });

  it("links an unhealthy workload to the Logs section on the page", async () => {
    listMock.mockResolvedValue([
      inSyncRow({
        health: "unhealthy",
        last_error: "container app is not running (exited, exit 1)",
      }),
    ]);
    const logs = document.createElement("div");
    logs.id = "device-logs";
    logs.scrollIntoView = vi.fn();
    document.body.appendChild(logs);
    renderPanel({ logsAvailable: true });

    const link = await screen.findByRole("link", { name: "View logs" });
    expect(link).toHaveAttribute("href", "#device-logs");
    fireEvent.click(link);
    expect(logs.scrollIntoView).toHaveBeenCalled();
    expect(screen.getByText("Unhealthy")).toBeInTheDocument();
    logs.remove();
  });

  it("has no logs link when the user cannot view logs", async () => {
    listMock.mockResolvedValue([inSyncRow({ health: "unhealthy", last_error: "down" })]);
    renderPanel();
    expect(await screen.findByText("Unhealthy")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View logs" })).not.toBeInTheDocument();
  });
});
