import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SoftwareDetailPage } from "@/pages/fleet/SoftwareDetailPage";

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ token: "t" }) }));
vi.mock("@/api/organizations", () => ({
  getOrganization: vi.fn().mockResolvedValue({ current_user_role: "operator" }),
}));
vi.mock("@/api/access", () => ({ getMyAccess: vi.fn() }));
vi.mock("@/components/fleet/DeviceLogs", () => ({
  DeviceLogs: ({ only }: { only: (s: object) => boolean }) => (
    <p>
      logs {String(only({ kind: "docker", name: "shop-web-1", project: "shop" }))}{" "}
      {String(only({ kind: "docker", name: "other-1", project: "other" }))}
    </p>
  ),
}));
vi.mock("@/api/fleet", async () => ({
  ...(await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet")),
  getDevice: vi.fn(),
  getDeviceSoftware: vi.fn(),
  listSoftwareActions: vi.fn(),
  orderSoftwareAction: vi.fn(),
  getSoftwareHistory: vi.fn(),
}));

import { getMyAccess } from "@/api/access";
import {
  getDevice,
  getDeviceSoftware,
  getSoftwareHistory,
  listSoftwareActions,
  orderSoftwareAction,
  type DeviceSoftwareDetail,
} from "@/api/fleet";
import { ApiError } from "@/api/http";

function container(service: string, running = true) {
  return {
    id: service,
    name: `shop-${service}-1`,
    metric: `shop-${service}-1`,
    service,
    release: "",
    state: running ? "running" : "exited",
    running,
    health: "",
    restarts: 2,
    exit_code: 0,
    oom_killed: false,
    started_at: "2026-11-24T10:00:00Z",
    finished_at: "0001-01-01T00:00:00Z",
    image: "nginx:1",
    image_id: "sha256:1",
    ports: [{ host_ip: "0.0.0.0", host_port: "8080", container: "80/tcp" }],
    mounts: [],
    networks: ["shop_default"],
  };
}

function detail(overrides: Partial<DeviceSoftwareDetail> = {}): DeviceSoftwareDetail {
  return {
    id: "sw-1",
    organization_id: "org-1",
    device_id: "dev-1",
    workload_key: "shop",
    kind: "docker_compose",
    desired_release_id: "rel-1",
    desired_revision: 1,
    desired_spec: {},
    desired_release: { id: "rel-1", name: "shop", version: "2.0", kind: "docker_compose" },
    installed_release_id: "rel-1",
    installed_version: "2.0",
    installed_release: { id: "rel-1", name: "shop", version: "2.0", kind: "docker_compose" },
    health: "healthy",
    sync_status: "in_sync",
    progress_percent: 100,
    attempt_count: 1,
    last_error: null,
    stopped_by_user: false,
    reported_at: null,
    created_at: "2026-11-24T10:00:00Z",
    updated_at: "2026-11-24T10:00:00Z",
    snapshot: {
      workload_key: "shop",
      kind: "docker_compose",
      taken_at: "2026-11-24T10:00:00Z",
      managed: true,
      note: "",
      truncated: false,
      stopped_services: [],
      containers: [container("web"), container("db")],
      images: [],
      volumes: [{ name: "shop_data", driver: "local" }],
      networks: [],
      unit: null,
    },
    snapshot_at: new Date().toISOString(),
    supported_actions: ["workload_restart", "workload_snapshot", "workload_start", "workload_stop"],
    can_manage: true,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/organizations/org-1/devices/dev-1/software/sw-1"]}>
        <Routes>
          <Route
            path="organizations/:organizationId/devices/:deviceId/software/:softwareId"
            element={<SoftwareDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const openTab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const button = (name: string) => screen.queryByRole("button", { name });

describe("SoftwareDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMyAccess).mockResolvedValue({
      organizations: [{ organization_id: "org-1", permissions: ["device.logs"] }],
    } as never);
    vi.mocked(getDevice).mockResolvedValue({
      name: "kiosk",
      status: "online",
    } as never);
    vi.mocked(getDeviceSoftware).mockResolvedValue(detail());
    vi.mocked(listSoftwareActions).mockResolvedValue([]);
    vi.mocked(orderSoftwareAction).mockResolvedValue({} as never);
    window.confirm = vi.fn(() => true);
  });

  it("shows services and volumes and stops one service after asking", async () => {
    renderPage();
    expect(await screen.findByText("shop-web-1")).toBeInTheDocument();
    expect(screen.getByText("shop_data")).toBeInTheDocument();
    expect(screen.getAllByText("8080 → 80/tcp")).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Stop service" })[0]);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("Stop web?"));
    await waitFor(() =>
      expect(orderSoftwareAction).toHaveBeenCalledWith("t", "org-1", "dev-1", "sw-1", {
        kind: "workload_stop",
        service: "web",
      }),
    );
  });

  it("does nothing when the question is declined", async () => {
    window.confirm = vi.fn(() => false);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Restart" }));
    expect(orderSoftwareAction).not.toHaveBeenCalled();
  });

  it("shows why an order was refused", async () => {
    vi.mocked(orderSoftwareAction).mockRejectedValue(
      new ApiError(429, "too_many_software_actions", "Try again shortly."),
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Restart" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again shortly.");
  });

  it.each([
    ["no permission", { can_manage: false }],
    ["an older agent", { supported_actions: [] }],
    [
      "a workload the device would refuse",
      { snapshot: { ...detail().snapshot!, managed: false, note: "Deploy again to enable." } },
    ],
  ])("has no buttons with %s", async (_, overrides) => {
    vi.mocked(getDeviceSoftware).mockResolvedValue(detail(overrides));
    renderPage();
    await screen.findByText("shop-web-1");
    for (const name of ["Restart", "Stop", "Start", "Stop service"]) {
      expect(button(name)).toBeNull();
    }
  });

  it("offers Start for stopped software", async () => {
    vi.mocked(getDeviceSoftware).mockResolvedValue(
      detail({ stopped_by_user: true, health: "unknown" }),
    );
    renderPage();
    expect(await screen.findByText("Stopped by user")).toBeInTheDocument();
    expect(button("Stop")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(orderSoftwareAction).toHaveBeenCalledWith("t", "org-1", "dev-1", "sw-1", {
        kind: "workload_start",
      }),
    );
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it("shows only this workload's logs, and no tab without the permission", async () => {
    const view = renderPage();
    await screen.findByText("shop-web-1");
    openTab("Logs");
    expect(screen.getByText("logs true false")).toBeInTheDocument();
    view.unmount();
    vi.mocked(getMyAccess).mockResolvedValue({
      organizations: [{ organization_id: "org-1", permissions: [] }],
    } as never);
    renderPage();
    await screen.findByText("shop-web-1");
    await waitFor(() => expect(screen.queryByRole("tab", { name: "Logs" })).toBeNull());
  });

  it("lists activity and says what is waiting", async () => {
    vi.mocked(listSoftwareActions).mockResolvedValue([
      {
        id: "a-1",
        kind: "workload_restart",
        state: "sent",
        service: "web",
        last_error: null,
        requested_by: "owner@example.com",
        created_at: "2026-11-24T10:00:00Z",
        sent_at: null,
        reported_at: null,
      },
    ]);
    renderPage();
    expect(await screen.findByText("Restart web: running")).toBeInTheDocument();
    openTab("Activity");
    expect(screen.getByText("owner@example.com")).toBeInTheDocument();
  });

  it("shows health history, and says when the data plane is missing", async () => {
    vi.mocked(getSoftwareHistory).mockResolvedValue({
      software_id: "sw-1",
      range: "24h",
      start: "2026-11-23T10:00:00Z",
      end: "2026-11-24T10:00:00Z",
      healthy_percent: 75,
      segments: [
        { start: "2026-11-23T10:00:00Z", end: "2026-11-24T04:00:00Z", status: "healthy" },
        { start: "2026-11-24T04:00:00Z", end: "2026-11-24T10:00:00Z", status: "unhealthy" },
      ],
      changes: [{ t: "2026-11-24T04:00:00Z", status: "unhealthy", reason: "exit 1" }],
      events: [],
      deployments: [],
      bucket_seconds: 900,
      series: [
        {
          name: "shop-web-1.mem",
          points: [
            { t: "2026-11-24T03:00:00Z", avg: 52428800, min: 52428800, max: 52428800, count: 1 },
          ],
        },
      ],
    });
    const view = renderPage();
    await screen.findByText("shop-web-1");
    openTab("Health");
    expect(await screen.findByText("75.0% healthy")).toBeInTheDocument();
    expect(screen.getByText("exit 1")).toBeInTheDocument();
    expect(screen.getByText("shop-web-1 memory")).toBeInTheDocument();
    expect(screen.getAllByText(/50\.0 MiB/).length).toBeGreaterThan(0);
    view.unmount();

    vi.mocked(getSoftwareHistory).mockRejectedValue(
      new ApiError(503, "telemetry_unavailable", "off"),
    );
    renderPage();
    await screen.findByText("shop-web-1");
    openTab("Health");
    expect(await screen.findByText(/History is not enabled/)).toBeInTheDocument();
  });
});
