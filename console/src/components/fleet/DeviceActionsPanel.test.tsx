import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  addDeviceUser,
  listDeviceActions,
  rebootDevice,
  setDevicePassword,
  shutdownDevice,
  type DeviceActions,
} from "@/api/fleet";
import { DeviceActionsPanel } from "@/components/fleet/DeviceActionsPanel";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    listDeviceActions: vi.fn(),
    rebootDevice: vi.fn(),
    shutdownDevice: vi.fn(),
    setDevicePassword: vi.fn(),
    addDeviceUser: vi.fn(),
  };
});

const empty: DeviceActions = { actions: [] };

function setup(props: Partial<Parameters<typeof DeviceActionsPanel>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DeviceActionsPanel
        token="token"
        organizationId="org-1"
        deviceId="dev-1"
        canPower
        canUsers
        {...props}
      />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

beforeEach(() => {
  vi.mocked(listDeviceActions).mockReset().mockResolvedValue(empty);
  vi.mocked(rebootDevice).mockReset().mockResolvedValue(empty);
  vi.mocked(shutdownDevice).mockReset().mockResolvedValue(empty);
  vi.mocked(setDevicePassword).mockReset().mockResolvedValue(empty);
  vi.mocked(addDeviceUser).mockReset().mockResolvedValue(empty);
});

describe("DeviceActionsPanel", () => {
  it("orders a restart after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = setup();
    await user.click(await screen.findByRole("button", { name: "Restart" }));
    await waitFor(() => expect(rebootDevice).toHaveBeenCalledWith("token", "org-1", "dev-1"));
  });

  it("refreshes the action list on demand", async () => {
    const user = setup();
    await screen.findByRole("button", { name: "Restart" });
    vi.mocked(listDeviceActions).mockClear();
    await user.click(screen.getByRole("button", { name: "Refresh actions" }));
    await waitFor(() => expect(listDeviceActions).toHaveBeenCalled());
  });

  it("shows a queued restart in the panel after success", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(rebootDevice).mockResolvedValue({
      actions: [
        {
          id: "a1",
          kind: "reboot",
          state: "pending",
          username: null,
          sudo: false,
          last_error: null,
          created_at: "2026-01-01T00:00:00Z",
          sent_at: null,
          reported_at: null,
        },
      ],
    });
    const user = setup();
    await user.click(await screen.findByRole("button", { name: "Restart" }));
    expect(await screen.findByText("Waiting for the device (its next heartbeat).")).toBeInTheDocument();
  });

  it("sends a password change", async () => {
    const user = setup();
    await user.click(await screen.findByRole("button", { name: "Change SSH password" }));
    await user.type(screen.getByLabelText("Username"), "pi");
    await user.type(screen.getByLabelText("Password"), "new-password");
    await user.click(screen.getByRole("button", { name: "Send to device" }));
    await waitFor(() =>
      expect(setDevicePassword).toHaveBeenCalledWith("token", "org-1", "dev-1", {
        username: "pi",
        password: "new-password",
      }),
    );
  });

  it("hides SSH user controls without the permission", async () => {
    setup({ canUsers: false });
    expect(await screen.findByRole("button", { name: "Restart" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change SSH password" })).not.toBeInTheDocument();
  });
});
