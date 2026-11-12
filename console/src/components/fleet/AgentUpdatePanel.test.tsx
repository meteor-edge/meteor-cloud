import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  listDeviceActions,
  updateDeviceAgent,
  type DeviceActions,
} from "@/api/fleet";
import { AgentUpdatePanel } from "@/components/fleet/AgentUpdatePanel";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    listDeviceActions: vi.fn(),
    updateDeviceAgent: vi.fn(),
  };
});

const empty: DeviceActions = { actions: [] };

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AgentUpdatePanel
        token="token"
        organizationId="org-1"
        deviceId="dev-1"
        agentVersion="0.3.3"
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(listDeviceActions).mockReset().mockResolvedValue(empty);
  vi.mocked(updateDeviceAgent).mockReset().mockResolvedValue(empty);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("AgentUpdatePanel", () => {
  it("shows the installed version and orders an update", async () => {
    const user = userEvent.setup();
    setup();
    expect(screen.getByText(/Installed version/)).toBeInTheDocument();
    expect(screen.getByText("0.3.3")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Update to latest" }));
    await waitFor(() => expect(updateDeviceAgent).toHaveBeenCalledWith("token", "org-1", "dev-1"));
  });

  it("hides the confirm path when the operator cancels", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Update to latest" }));
    expect(updateDeviceAgent).not.toHaveBeenCalled();
  });
});
