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

  it("keeps the mutation result when a stale list response arrives later", async () => {
    let resolveList: ((value: DeviceActions) => void) | undefined;
    const pendingList = new Promise<DeviceActions>((resolve) => {
      resolveList = resolve;
    });
    vi.mocked(listDeviceActions).mockReturnValueOnce(pendingList);
    const ordered: DeviceActions = {
      actions: [
        {
          id: "act-1",
          kind: "update_agent",
          state: "pending",
          from_version: "0.3.3",
          username: null,
          sudo: false,
          created_at: "2026-01-01T00:00:00Z",
          sent_at: null,
          reported_at: null,
          last_error: null,
        },
      ],
    };
    vi.mocked(updateDeviceAgent).mockResolvedValue(ordered);
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Update to latest" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Updating…" })).toBeInTheDocument());
    resolveList?.(empty);
    await waitFor(() => expect(screen.getByRole("button", { name: "Updating…" })).toBeInTheDocument());
  });
});
