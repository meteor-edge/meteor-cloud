import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as historyApi from "@/api/fleet/history";
import { ApiError } from "@/api/http";
import {
  AvailabilityStrip,
  DeploymentHistoryPanel,
  DeviceAppsPanel,
  DeviceLogHistory,
} from "@/components/fleet/DeviceHistoryPanels";
import { FleetOnlineChart } from "@/components/fleet/FleetOnlineChart";
import { fillBuckets } from "@/lib/telemetry";

vi.mock("@/api/fleet/history", () => ({
  getDeviceAvailability: vi.fn(),
  getDeviceDeploymentHistory: vi.fn(),
  getDeviceApps: vi.fn(),
  getDeviceLogHistory: vi.fn(),
  getFleetOnlineHistory: vi.fn(),
}));

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const props = { token: "t", organizationId: "org", deviceId: "dev" };

describe("device history panels", () => {
  // Braces matter: a function returned from beforeEach runs as a teardown.
  beforeEach(() => {
    vi.resetAllMocks();
  });
  afterEach(cleanup);

  it("shows uptime and offline count on the availability strip", async () => {
    vi.mocked(historyApi.getDeviceAvailability).mockResolvedValue({
      device_id: "dev",
      range: "24h",
      start: "2026-11-15T00:00:00Z",
      end: "2026-11-16T00:00:00Z",
      uptime_percent: 95.83,
      offline_count: 1,
      segments: [
        { start: "2026-11-15T00:00:00Z", end: "2026-11-15T10:00:00Z", status: "online" },
        { start: "2026-11-15T10:00:00Z", end: "2026-11-15T11:00:00Z", status: "offline" },
        { start: "2026-11-15T11:00:00Z", end: "2026-11-16T00:00:00Z", status: "online" },
      ],
      changes: [],
    });
    wrap(<AvailabilityStrip {...props} range="24h" />);
    expect(
      await screen.findByRole("img", { name: /95.8% online · went offline 1 time$/ }),
    ).toBeInTheDocument();
    expect(historyApi.getDeviceAvailability).toHaveBeenCalledWith("t", "org", "dev", "24h");
  });

  it("lists deployment changes with release and duration", async () => {
    vi.mocked(historyApi.getDeviceDeploymentHistory).mockResolvedValue({
      device_id: "dev",
      items: [
        {
          t: "2026-11-15T10:00:00Z",
          status: "failed",
          previous_status: "installing",
          deployment_id: "d1",
          deployment_name: "Camera rollout",
          release_id: "r1",
          release_name: "camera-agent",
          release_version: "1.2.0",
          seconds: 200,
          error: "health check failed",
        },
      ],
    });
    wrap(<DeploymentHistoryPanel {...props} />);
    expect(await screen.findByText("camera-agent 1.2.0")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("health check failed")).toBeInTheDocument();
    expect(screen.getByText("3 min 20 s")).toBeInTheDocument();
  });

  it("explains when history is not enabled", async () => {
    vi.mocked(historyApi.getDeviceDeploymentHistory).mockRejectedValue(
      new ApiError(503, "telemetry_unavailable", "no data API"),
    );
    wrap(<DeploymentHistoryPanel {...props} />);
    expect(await screen.findByText(/History is not enabled/)).toBeInTheDocument();
  });

  it("shows apps and their recent lifecycle changes", async () => {
    vi.mocked(historyApi.getDeviceApps).mockResolvedValue({
      device_id: "dev",
      reported_at: "2026-11-15T10:00:00Z",
      apps: [
        {
          name: "web",
          cpu_percent: 3.5,
          memory_bytes: 10 * 1024 * 1024,
          restarts: 2,
          healthy: false,
          health_ms: 12,
          running: true,
        },
      ],
      events: [
        {
          t: "2026-11-15T09:00:00Z",
          type: "app.exited",
          app: "web",
          exit_code: 137,
          oom_killed: true,
          detail: null,
        },
      ],
    });
    wrap(<DeviceAppsPanel {...props} />);
    expect(await screen.findByText("Unhealthy")).toBeInTheDocument();
    expect(screen.getByText("3.5%")).toBeInTheDocument();
    expect(screen.getByText("10.0 MB")).toBeInTheDocument();
    expect(screen.getByText("Exited · exit 137 · out of memory")).toBeInTheDocument();
  });

  it("renders nothing for a device without apps", async () => {
    vi.mocked(historyApi.getDeviceApps).mockResolvedValue({
      device_id: "dev",
      reported_at: null,
      apps: [],
      events: [],
    });
    const { container } = wrap(<DeviceAppsPanel {...props} />);
    await waitFor(() => expect(historyApi.getDeviceApps).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("searches saved warnings", async () => {
    vi.mocked(historyApi.getDeviceLogHistory).mockResolvedValue({
      device_id: "dev",
      range: "7d",
      query: null,
      entries: [
        {
          t: "2026-11-15T09:00:00Z",
          message: "disk failing",
          priority: 3,
          unit: "smartd.service",
          source: null,
        },
      ],
      next_cursor: null,
    });
    wrap(<DeviceLogHistory {...props} />);
    expect(await screen.findByText("disk failing")).toBeInTheDocument();
    expect(screen.getByText("error")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Search saved warnings" }), {
      target: { value: " disk " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() =>
      expect(historyApi.getDeviceLogHistory).toHaveBeenLastCalledWith("t", "org", "dev", {
        range: "7d",
        q: "disk",
        cursor: undefined,
      }),
    );
  });
});

describe("saved warnings paging", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });
  afterEach(cleanup);

  it("keeps loaded warnings when an older page fails", async () => {
    vi.mocked(historyApi.getDeviceLogHistory)
      .mockResolvedValueOnce({
        device_id: "dev",
        range: "7d",
        query: null,
        entries: [
          {
            t: "2026-11-15T09:00:00Z",
            message: "first page",
            priority: 4,
            unit: null,
            source: null,
          },
        ],
        next_cursor: "c1",
      })
      .mockRejectedValueOnce(new ApiError(500, "storage_unavailable", "down"));
    wrap(<DeviceLogHistory {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older" }));
    expect(await screen.findByText("Older warnings could not be loaded.")).toBeInTheDocument();
    expect(screen.getByText("first page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("FleetOnlineChart", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });
  afterEach(cleanup);

  const history = {
    range: "24h" as const,
    start: "2026-11-15T00:00:00Z",
    end: "2026-11-15T01:00:00Z",
    bucket_seconds: 900,
    total_devices: 4,
    online_now: 3,
    buckets: [
      { t: "2026-11-15T00:00:00Z", devices: 2 },
      { t: "2026-11-15T00:30:00Z", devices: 3 },
    ],
  };

  it("fills empty buckets with zero", () => {
    expect(fillBuckets(history).map((b) => b.devices)).toEqual([2, 0, 3, 0]);
  });

  it("shows online now, total and peak, and switches range", async () => {
    vi.mocked(historyApi.getFleetOnlineHistory).mockResolvedValue(history);
    wrap(<FleetOnlineChart token="t" organizationId="org" />);
    expect(
      await screen.findByRole("img", {
        name: "Devices online over the last 24h: 3 of 4 now, peak 3",
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "7d" }));
    await waitFor(() =>
      expect(historyApi.getFleetOnlineHistory).toHaveBeenLastCalledWith("t", "org", "7d"),
    );
  });

  it("is hidden for members limited to some device groups", async () => {
    vi.mocked(historyApi.getFleetOnlineHistory).mockRejectedValue(
      new ApiError(403, "fleet_scope_required", "no"),
    );
    const { container } = wrap(<FleetOnlineChart token="t" organizationId="org" />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
