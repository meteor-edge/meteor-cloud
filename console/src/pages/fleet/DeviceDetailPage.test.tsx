import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Device } from "@/api/fleet/types";
import { DeviceDetailPage } from "@/pages/fleet/DeviceDetailPage";

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

const device: Device = {
  id: "dev-1",
  organization_id: "org-1",
  name: "edge-01",
  device_type_id: null,
  device_group_id: null,
  is_enabled: true,
  status: "online",
  serial_number: null,
  mac_addresses: [],
  hostname: "edge-01.local",
  os_name: null,
  os_version: null,
  kernel_version: null,
  architecture: null,
  cpu_model: null,
  cpu_cores: null,
  memory_mb: null,
  labels: {},
  metadata: {},
  credential_prefix: null,
  last_seen_at: null,
  registered_at: null,
  created_at: "2026-10-26T09:00:00Z",
  updated_at: "2026-10-26T09:00:00Z",
  mqtt_configured: false,
  mqtt_status: null,
  mqtt_status_at: null,
  mqtt_metrics: null,
  mqtt_metrics_at: null,
};

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    getDevice: vi.fn(async () => device),
    listDeviceTypes: vi.fn(async () => []),
    listDeviceGroups: vi.fn(async () => []),
    getLogSources: vi.fn(),
  };
});

// The panels make their own requests; only the Logs window matters here.
vi.mock("@/components/fleet/DeviceSoftwarePanel", () => ({ DeviceSoftwarePanel: () => null }));
vi.mock("@/components/fleet/DeviceEndpointsPanel", () => ({ DeviceEndpointsPanel: () => null }));
vi.mock("@/components/fleet/DeviceNetworkPanel", () => ({ DeviceNetworkPanel: () => null }));
vi.mock("@/components/fleet/MqttConsole", () => ({ MqttConsole: () => null }));
vi.mock("@/components/fleet/DeviceLogs", () => ({
  DeviceLogs: () => <p>logs window</p>,
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/organizations/org-1/devices/dev-1"]}>
        <Routes>
          <Route
            path="/organizations/:organizationId/devices/:deviceId"
            element={<DeviceDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DeviceDetailPage logs", () => {
  it("opens logs from the Access tab only when the user asks", async () => {
    renderPage();

    expect(screen.queryByRole("button", { name: "Open logs" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("tab", { name: "Access" }));
    const open = await screen.findByRole("button", { name: "Open logs" });
    expect(screen.queryByText("logs window")).not.toBeInTheDocument();

    fireEvent.click(open);
    expect(screen.getByText("logs window")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close logs" }));
    expect(screen.queryByText("logs window")).not.toBeInTheDocument();
  });
});
