import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/http";
import { DeviceNetworkPanel } from "@/components/fleet/DeviceNetworkPanel";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return { ...actual, getDeviceConnectivity: vi.fn() };
});

import { getDeviceConnectivity, type DeviceConnectivity } from "@/api/fleet";

const getMock = vi.mocked(getDeviceConnectivity);

const connectivity: DeviceConnectivity = {
  device_id: "dev-1",
  organization_id: "org-1",
  provider: "wireguard",
  wg_public_key: "key",
  wg_address: "10.8.0.2",
  status: "active",
  last_handshake_at: "2026-10-23T10:00:00Z",
  connected: true,
  server_address: "10.8.0.1",
  ssh_user: "root",
  ssh_port: 22,
  revoked_at: null,
  created_at: "2026-10-23T09:00:00Z",
  updated_at: "2026-10-23T10:00:00Z",
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeviceNetworkPanel token="t" organizationId="org-1" deviceId="dev-1" />
    </QueryClientProvider>,
  );
}

describe("DeviceNetworkPanel", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("shows the internal IP and the SSH command", async () => {
    getMock.mockResolvedValue(connectivity);
    renderPanel();
    expect(await screen.findByText("10.8.0.2")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("meteorcloud-ssh root@10.8.0.2")).toBeInTheDocument();
  });

  it("adds the port when SSH is not on 22", async () => {
    getMock.mockResolvedValue({ ...connectivity, ssh_port: 2222, connected: false });
    renderPanel();
    expect(await screen.findByText("meteorcloud-ssh -p 2222 root@10.8.0.2")).toBeInTheDocument();
    expect(screen.getByText("Waiting for the device")).toBeInTheDocument();
  });

  it("explains when the device has not joined yet", async () => {
    getMock.mockRejectedValue(new ApiError(404, "connectivity_not_found", "not yet"));
    renderPanel();
    expect(await screen.findByText(/has not joined the WireGuard network/)).toBeInTheDocument();
  });
});
