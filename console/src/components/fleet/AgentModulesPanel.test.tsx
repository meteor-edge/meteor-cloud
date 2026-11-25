import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  listDeviceActions,
  restartDeviceModule,
  runDeviceDiagnostics,
  type DeviceAction,
  type DeviceActions,
} from "@/api/fleet";
import { AgentModulesPanel } from "@/components/fleet/AgentModulesPanel";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    listDeviceActions: vi.fn(),
    restartDeviceModule: vi.fn(),
    runDeviceDiagnostics: vi.fn(),
  };
});

const goCapabilities = {
  actions: ["reboot", "update_agent", "restart_module", "run_diagnostics"],
  deploy_kinds: ["docker"],
  modules: {
    heartbeat: { state: "running", restarts: 0 },
    deploy: { state: "parked", restarts: 5, last_error: "artifacts directory not writable" },
  },
};

function action(overrides: Partial<DeviceAction>): DeviceAction {
  return {
    id: "a-1",
    kind: "restart_module",
    state: "pending",
    username: null,
    sudo: false,
    last_error: null,
    created_at: "2026-11-22T10:00:00Z",
    sent_at: null,
    reported_at: null,
    ...overrides,
  };
}

function setup(capabilities: Record<string, unknown> | undefined, canRepair = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AgentModulesPanel
        token="token"
        organizationId="org-1"
        deviceId="dev-1"
        device={{ status: "online", metadata: capabilities ? { capabilities } : {} }}
        canRepair={canRepair}
      />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

const empty: DeviceActions = { actions: [] };

beforeEach(() => {
  vi.mocked(listDeviceActions).mockReset().mockResolvedValue(empty);
  vi.mocked(restartDeviceModule).mockReset().mockResolvedValue(empty);
  vi.mocked(runDeviceDiagnostics).mockReset().mockResolvedValue(empty);
});

describe("AgentModulesPanel", () => {
  it("shows a parked module as failed, first, with its error, and restarts it", async () => {
    const user = setup(goCapabilities);
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("deploy");
    const deploy = screen.getByTestId("module-deploy");
    expect(within(deploy).getByText("Failed (stopped)")).toBeInTheDocument();
    expect(within(deploy).getByText("artifacts directory not writable")).toBeInTheDocument();
    expect(within(screen.getByTestId("module-heartbeat")).getByText("Running")).toBeInTheDocument();

    await user.click(within(deploy).getByRole("button", { name: "Restart" }));
    await waitFor(() =>
      expect(restartDeviceModule).toHaveBeenCalledWith("token", "org-1", "dev-1", "deploy"),
    );
  });

  it("runs diagnostics and shows the report", async () => {
    vi.mocked(listDeviceActions).mockResolvedValue({
      actions: [
        action({
          id: "d-1",
          kind: "run_diagnostics",
          state: "done",
          output: "[ok  ] server  answers\nNo problems found.\n",
          reported_at: "2026-11-22T10:01:00Z",
        }),
      ],
    });
    const user = setup(goCapabilities);
    expect(await screen.findByText(/No problems found/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run diagnostics" }));
    await waitFor(() =>
      expect(runDeviceDiagnostics).toHaveBeenCalledWith("token", "org-1", "dev-1"),
    );
  });

  it("shows a pending restart and disables a second one", async () => {
    vi.mocked(listDeviceActions).mockResolvedValue({
      actions: [action({ module: "deploy" })],
    });
    setup(goCapabilities);
    const deploy = screen.getByTestId("module-deploy");
    expect(await within(deploy).findByText(/Restart waiting for the device/)).toBeInTheDocument();
    expect(within(deploy).getByRole("button", { name: "Restart" })).toBeDisabled();
  });

  it("offers no repair to an agent without the actions or to viewers", () => {
    setup({ ...goCapabilities, actions: ["reboot"] });
    expect(screen.queryByRole("button", { name: "Restart" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run diagnostics" })).not.toBeInTheDocument();
  });

  it("does not load actions for viewers", () => {
    setup(goCapabilities, false);
    expect(screen.queryByRole("button", { name: "Restart" })).not.toBeInTheDocument();
    expect(listDeviceActions).not.toHaveBeenCalled();
  });

  it("sums up module health and shows healthy modules as compact tiles", () => {
    setup({
      ...goCapabilities,
      modules: { heartbeat: { state: "running" }, mqtt: { state: "running" } },
    });
    expect(screen.getByText("All 2 running")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("module-mqtt")).getByRole("button", { name: "Restart" }),
    ).toHaveAttribute("title", "Restart mqtt");
  });

  it("describes each known module: a tooltip on tiles, a line on problem rows", () => {
    setup(goCapabilities);
    const tip = within(screen.getByTestId("module-heartbeat")).getByRole("tooltip", {
      hidden: true,
    });
    expect(tip).toHaveTextContent(/alive/);
    expect(screen.getByText(/alive/).id).toBe("module-tip-heartbeat");
    expect(
      within(screen.getByTestId("module-deploy")).getByText(/Installs the software/),
    ).toBeInTheDocument();
  });

  it("counts broken modules in the summary", () => {
    setup(goCapabilities);
    expect(screen.getByText("1 needs attention")).toBeInTheDocument();
  });

  it("explains when the agent reports no modules", () => {
    setup({ docker: true });
    expect(screen.getByText(/does not report module health/)).toBeInTheDocument();
  });
});
