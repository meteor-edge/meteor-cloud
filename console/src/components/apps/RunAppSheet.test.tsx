import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDeployment, listDevices, listEndpoints, runApp, suggestHostname } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getRegistry, listImages, listImageTags } from "@/api/registry";
import { RunAppSheet, runState } from "@/components/apps/RunAppSheet";

vi.mock("@/api/fleet", () => ({
  runApp: vi.fn(),
  getDeployment: vi.fn(),
  listDevices: vi.fn(),
  listEndpoints: vi.fn(),
  suggestHostname: vi.fn(),
  checkHostname: vi.fn(),
}));
vi.mock("@/api/registry", () => ({
  getRegistry: vi.fn(),
  listImages: vi.fn(),
  listImageTags: vi.fn(),
}));

function deployment(status: string, health: string | null = null, last_error: string | null = null) {
  return { id: "dep-1", devices: [{ status, health, last_error }] } as never;
}

function renderSheet(props: Partial<Parameters<typeof RunAppSheet>[0]> = {}) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <RunAppSheet
          token="t"
          organizationId="org-1"
          deviceId="dev-1"
          canPublish
          onClose={vi.fn()}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RunAppSheet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getRegistry).mockResolvedValue({ enabled: true, host: "r", namespace: "acme" });
    vi.mocked(listImages).mockResolvedValue({
      items: [{ repository: "team/web", tag_count: 2, pushed_at: "" }],
      total: 1,
      page: 1,
      page_size: 100,
    });
    vi.mocked(listImageTags).mockResolvedValue({
      items: [{ tag: "2.0" }, { tag: "1.0" }] as never,
      total: 2,
      page: 1,
      page_size: 100,
    });
    vi.mocked(listDevices).mockResolvedValue({
      items: [{ id: "dev-2", name: "pi", status: "online" }],
    } as never);
    vi.mocked(suggestHostname).mockResolvedValue({
      hostname: "web.pi.example.com",
      available: true,
      custom_domain: "example.com",
    } as never);
  });

  it("runs the newest tag with default ports, follows the Deploy and opens the URL", async () => {
    vi.mocked(runApp).mockResolvedValue({
      workload_key: "web",
      release_id: "rel-1",
      deployment: deployment("pending"),
    });
    vi.mocked(getDeployment).mockResolvedValue(deployment("succeeded", "healthy"));
    vi.mocked(listEndpoints).mockResolvedValue([
      { workload_key: "web", availability: "ready", public_url: "https://web.pi.example.com" },
    ] as never);
    renderSheet();
    await screen.findByRole("option", { name: "team/web" });
    fireEvent.change(screen.getByLabelText("Image"), { target: { value: "team/web" } });
    await waitFor(() => expect(screen.getByLabelText("Tag")).toHaveValue("2.0"));
    fireEvent.click(screen.getByLabelText("Public URL"));
    await screen.findByText("web.pi.example.com");
    await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Open URL" })).toHaveAttribute(
      "href",
      "https://web.pi.example.com",
    );
    expect(runApp).toHaveBeenCalledWith("t", "org-1", "dev-1", {
      image: "team/web:2.0",
      registry: "meteorcloud",
      name: "web",
      ports: ["8080:8080"],
      env: {},
      endpoint: { hostname: "web.pi.example.com", port: 8080 },
    });
  });

  it("runs a given image on a chosen device", async () => {
    vi.mocked(runApp).mockRejectedValue(new ApiError(409, "app_exists", "Already runs web."));
    renderSheet({ deviceId: undefined, image: "web:1.0", canPublish: false });
    await screen.findByRole("option", { name: "pi (online)" });
    fireEvent.change(screen.getByLabelText("Device"), { target: { value: "dev-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Add variable" }));
    fireEvent.change(screen.getByLabelText("Variable 1 name"), { target: { value: "KEY" } });
    fireEvent.change(screen.getByLabelText("Variable 1 value"), { target: { value: "v" } });
    expect(screen.getByLabelText("Variable 1 value")).toHaveAttribute("type", "password");
    await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Already runs web.")).toBeInTheDocument();
    expect(vi.mocked(runApp).mock.calls[0][2]).toBe("dev-2");
    expect(vi.mocked(runApp).mock.calls[0][3].env).toEqual({ KEY: "v" });
  });

  it("refuses bad ports and takes public images when the registry is off", async () => {
    vi.mocked(getRegistry).mockResolvedValue({ enabled: false, host: null, namespace: null });
    renderSheet();
    fireEvent.change(await screen.findByLabelText("Image"), { target: { value: "nginx:1" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Device port 1"), { target: { value: "99999" } });
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("names every state", () => {
    expect(runState(undefined).label).toBe("Waiting for the device");
    expect(runState({ status: "updating", health: null }).label).toBe("Pulling and starting");
    expect(runState({ status: "succeeded", health: "unknown" }).done).toBe(false);
    expect(runState({ status: "succeeded", health: "unhealthy" }).failed).toBe(true);
    expect(runState({ status: "failed", health: null }).failed).toBe(true);
  });
});
