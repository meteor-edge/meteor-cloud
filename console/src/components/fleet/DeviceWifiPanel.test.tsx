import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDeviceWifi,
  removeDeviceWifiNetwork,
  setDeviceWifiNetwork,
  type DeviceWifi,
} from "@/api/fleet";
import { DeviceWifiPanel } from "@/components/fleet/DeviceWifiPanel";

vi.mock("@/api/fleet", () => ({
  getDeviceWifi: vi.fn(),
  setDeviceWifiNetwork: vi.fn(),
  removeDeviceWifiNetwork: vi.fn(),
}));

const reported: DeviceWifi = {
  status: {
    supported: true,
    unsupported_reason: null,
    connected_ssid: "Home",
    signal_percent: 71,
    interface: "wlan0",
    wired_connected: false,
    saved_networks: [{ ssid: "Home", managed: false }],
    uplink_type: "wifi",
    uplink_interface: "wlan0",
    reported_at: "2026-11-11T10:00:00Z",
  },
  networks: [],
};

function network(
  overrides: Partial<DeviceWifi["networks"][number]>,
): DeviceWifi["networks"][number] {
  return {
    id: "net-1",
    ssid: "Office",
    security: "wpa-psk",
    hidden: false,
    connect_now: true,
    action: "apply",
    state: "pending",
    last_error: null,
    reported_at: null,
    updated_at: "2026-11-11T10:01:00Z",
    password_pending: true,
    ...overrides,
  };
}

function setup(wifi: DeviceWifi) {
  vi.mocked(getDeviceWifi).mockResolvedValue(wifi);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DeviceWifiPanel token="token" organizationId="org-1" deviceId="dev-1" />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

beforeEach(() => {
  vi.mocked(getDeviceWifi).mockReset();
  vi.mocked(setDeviceWifiNetwork).mockReset();
  vi.mocked(removeDeviceWifiNetwork).mockReset();
});

describe("DeviceWifiPanel", () => {
  it("shows what the device reports", async () => {
    setup(reported);
    expect(await screen.findByText("Home (signal 71%)")).toBeInTheDocument();
    expect(screen.getByText("In use")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
  });

  it("sends a new network to connect now, and shows it waiting", async () => {
    vi.mocked(setDeviceWifiNetwork).mockResolvedValue({ ...reported, networks: [network({})] });
    const user = setup(reported);
    await user.click(await screen.findByRole("button", { name: "Add a network" }));
    await user.type(screen.getByLabelText("Network name (SSID)"), "Office");
    await user.type(screen.getByLabelText("Wi-Fi password"), "office-secret");
    await user.click(screen.getByRole("button", { name: "Send to device" }));

    await waitFor(() =>
      expect(setDeviceWifiNetwork).toHaveBeenCalledWith("token", "org-1", "dev-1", {
        ssid: "Office",
        security: "wpa-psk",
        password: "office-secret",
        hidden: false,
        connect_now: true,
      }),
    );
    expect(
      await screen.findByText(/Waiting for the device to pick up the change/),
    ).toBeInTheDocument();
    // The form is gone, and with it the typed password.
    expect(screen.queryByLabelText("Wi-Fi password")).not.toBeInTheDocument();
  });

  it("changes a password for later without switching networks", async () => {
    vi.mocked(setDeviceWifiNetwork).mockResolvedValue(reported);
    const user = setup(reported);
    await user.click(await screen.findByRole("button", { name: "Change password" }));
    expect(screen.getByLabelText("Network name (SSID)")).toBeDisabled();
    await user.type(screen.getByLabelText("Wi-Fi password"), "next-password");
    await user.click(screen.getByRole("radio", { name: /Save for later/ }));
    await user.click(screen.getByRole("button", { name: "Send to device" }));
    await waitFor(() =>
      expect(setDeviceWifiNetwork).toHaveBeenCalledWith(
        "token",
        "org-1",
        "dev-1",
        expect.objectContaining({ ssid: "Home", password: "next-password", connect_now: false }),
      ),
    );
  });

  it("refuses a short password before sending anything", async () => {
    const user = setup(reported);
    await user.click(await screen.findByRole("button", { name: "Add a network" }));
    await user.type(screen.getByLabelText("Network name (SSID)"), "Office");
    await user.type(screen.getByLabelText("Wi-Fi password"), "short");
    await user.click(screen.getByRole("button", { name: "Send to device" }));
    expect(screen.getByText("The Wi-Fi password must be 8 to 63 characters.")).toBeInTheDocument();
    expect(setDeviceWifiNetwork).not.toHaveBeenCalled();
  });

  it("shows why a change was rolled back", async () => {
    setup({
      ...reported,
      networks: [
        network({
          state: "rolled_back",
          password_pending: false,
          last_error:
            "The device could not join this network. The old Wi-Fi settings were put back.",
        }),
      ],
    });
    expect(await screen.findByText(/old Wi-Fi settings were put back/)).toBeInTheDocument();
  });

  it("removes a network after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(removeDeviceWifiNetwork).mockResolvedValue(reported);
    const user = setup(reported);
    await user.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(removeDeviceWifiNetwork).toHaveBeenCalledWith("token", "org-1", "dev-1", "Home"),
    );
  });

  it("explains a device that has not reported", async () => {
    setup({ status: null, networks: [] });
    expect(await screen.findByText(/needs meteorcli 0.3.0 or newer/)).toBeInTheDocument();
  });
});
