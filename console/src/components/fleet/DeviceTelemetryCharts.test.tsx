import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as telemetryApi from "@/api/fleet/telemetry";
import { ApiError } from "@/api/http";
import { DeviceTelemetryCharts } from "@/components/fleet/DeviceTelemetryCharts";
import { splitSegments } from "@/lib/telemetry";

vi.mock("@/api/fleet/telemetry", () => ({ getDeviceTelemetry: vi.fn() }));
vi.mock("@/components/fleet/DeviceHistoryPanels", () => ({ AvailabilityStrip: () => null }));

const start = "2026-11-14T10:00:00Z";
const end = "2026-11-14T11:00:00Z";

function point(minute: number, avg: number) {
  const t = new Date(Date.parse(start) + minute * 60_000).toISOString();
  return { t, avg, min: avg - 1, max: avg + 1, count: 1 };
}

function renderCharts() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeviceTelemetryCharts token="t" organizationId="org" deviceId="dev" />
    </QueryClientProvider>,
  );
}

describe("DeviceTelemetryCharts", () => {
  // Braces matter: a function returned from beforeEach runs as a teardown.
  beforeEach(() => {
    vi.mocked(telemetryApi.getDeviceTelemetry).mockReset();
  });
  afterEach(cleanup);

  it("charts each metric and switches range", async () => {
    vi.mocked(telemetryApi.getDeviceTelemetry).mockResolvedValue({
      device_id: "dev",
      range: "24h",
      start,
      end,
      bucket_seconds: 60,
      series: [
        { name: "cpu_percent", points: [point(0, 10), point(1, 30)] },
        { name: "memory_percent", points: [point(0, 40)] },
        { name: "disk_percent", points: [] },
        { name: "temperature_c", points: [point(0, 48.25)] },
        { name: "load_1m", points: [] },
      ],
    });
    renderCharts();

    expect(
      await screen.findByRole("img", { name: /CPU over the last 24h: latest 30.0%, peak 31.0%/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Disk: no data/ })).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Temperature over the last 24h: latest 48.3 °C/ }),
    ).toBeInTheDocument();
    // Load is hidden when the agent does not report it.
    expect(screen.queryByText("Load (1 min)")).not.toBeInTheDocument();

    fireEvent.pointerMove(screen.getByTestId("chart-cpu_percent"), { clientX: 0 });
    expect(screen.getByRole("status")).toHaveTextContent("10.0% avg");

    fireEvent.click(screen.getByRole("radio", { name: "7d" }));
    await waitFor(() =>
      expect(telemetryApi.getDeviceTelemetry).toHaveBeenLastCalledWith("t", "org", "dev", "7d"),
    );
  });

  it("explains when the installation has no telemetry store", async () => {
    vi.mocked(telemetryApi.getDeviceTelemetry).mockRejectedValue(
      new ApiError(503, "telemetry_unavailable", "Telemetry history needs the data API."),
    );
    renderCharts();
    await waitFor(() =>
      expect(screen.getByText(/not enabled on this installation/)).toBeInTheDocument(),
    );
  });

  it("splits lines where buckets are missing", () => {
    const segments = splitSegments([point(0, 1), point(1, 2), point(10, 3)], 60);
    expect(segments.map((s) => s.length)).toEqual([2, 1]);
  });
});
