import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/http";
import { DeviceNetworkPanel } from "@/components/fleet/DeviceNetworkPanel";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    getDeviceConnectivity: vi.fn(),
    reenrollDeviceConnectivity: vi.fn(),
    revokeDeviceConnectivity: vi.fn(),
  };
});

import {
  getDeviceConnectivity,
  reenrollDeviceConnectivity,
  type DeviceConnectivity,
} from "@/api/fleet";

const getMock = vi.mocked(getDeviceConnectivity);
const reenrollMock = vi.mocked(reenrollDeviceConnectivity);

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

function renderPanel(canManage = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeviceNetworkPanel token="t" organizationId="org-1" deviceId="dev-1" canManage={canManage} />
    </QueryClientProvider>,
  );
}

describe("DeviceNetworkPanel", () => {
  beforeEach(() => {
    getMock.mockReset();
    reenrollMock.mockReset();
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

  it("shows diagnostics and tells reconnecting from offline", async () => {
    getMock.mockResolvedValue({
      ...connectivity,
      connected: false,
      state: "reconnecting",
      agent_last_seen_at: "2026-10-23T10:05:00Z",
      ssh_host_key_fingerprint: "SHA256:abc",
      last_ssh_ok_at: null,
      last_ssh_error: "Host key verification failed.",
      last_ssh_error_at: "2026-10-23T10:06:00Z",
    });
    renderPanel();
    expect(await screen.findByText("Reconnecting")).toBeInTheDocument();
    expect(screen.getByText("SHA256:abc")).toBeInTheDocument();
    expect(screen.getByText(/Host key verification failed/)).toBeInTheDocument();
    expect(screen.queryByText("Re-enroll")).not.toBeInTheDocument(); // needs device.update
  });

  it("re-enrolls a revoked device after confirmation", async () => {
    getMock.mockResolvedValue({
      ...connectivity,
      status: "revoked",
      state: "revoked",
      wg_address: null,
      connected: false,
    });
    reenrollMock.mockResolvedValue({ ...connectivity, status: "pending", state: "pending", wg_address: null });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPanel(true);
    expect(await screen.findByText("Revoked")).toBeInTheDocument();
    expect(screen.queryByText("Revoke access")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Re-enroll"));
    expect(await screen.findByText("Waiting for the device")).toBeInTheDocument();
    expect(reenrollMock).toHaveBeenCalledWith("t", "org-1", "dev-1");
  });
});
