import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDeviceWifi, type DeviceWifi, type DeviceWifiStatus } from "@/api/fleet";
import { DeviceUplinkPanel } from "@/components/fleet/DeviceUplinkPanel";

vi.mock("@/api/fleet", () => ({ getDeviceWifi: vi.fn() }));

const base: DeviceWifiStatus = {
  supported: true,
  unsupported_reason: null,
  connected_ssid: "Home",
  signal_percent: 71,
  interface: "wlan0",
  wired_connected: false,
  saved_networks: [],
  uplink_type: "wifi",
  uplink_interface: "wlan0",
  reported_at: "2026-11-11T10:00:00Z",
};

function renderWith(status: DeviceWifiStatus | null) {
  const wifi: DeviceWifi = { status, networks: [] };
  vi.mocked(getDeviceWifi).mockResolvedValue(wifi);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DeviceUplinkPanel token="t" organizationId="org" deviceId="dev" />
    </QueryClientProvider>,
  );
}

describe("DeviceUplinkPanel", () => {
  beforeEach(() => vi.mocked(getDeviceWifi).mockReset());

  it("shows Wi-Fi with the network name and signal", async () => {
    renderWith(base);
    expect(await screen.findByText("Wi-Fi")).toBeInTheDocument();
    expect(screen.getByText(/Home, signal 71%/)).toBeInTheDocument();
    expect(screen.getByText("wlan0")).toBeInTheDocument();
  });

  it("shows Ethernet, and Wi-Fi that is also connected", async () => {
    renderWith({
      ...base,
      uplink_type: "ethernet",
      uplink_interface: "eth0",
      wired_connected: true,
    });
    expect(await screen.findByText("Ethernet")).toBeInTheDocument();
    expect(screen.getByText("eth0")).toBeInTheDocument();
    expect(screen.getByText("Wi-Fi also connected: Home, signal 71%.")).toBeInTheDocument();
  });

  it("works where Wi-Fi cannot be managed", async () => {
    renderWith({
      ...base,
      supported: false,
      unsupported_reason: "NetworkManager is not installed on this device.",
      connected_ssid: null,
      uplink_type: "ethernet",
      uplink_interface: "eth0",
    });
    expect(await screen.findByText("Ethernet")).toBeInTheDocument();
  });

  it("says when the device has not reported it", async () => {
    renderWith({ ...base, uplink_type: null, uplink_interface: null });
    expect(await screen.findByText(/Not reported yet/)).toBeInTheDocument();
  });
});
