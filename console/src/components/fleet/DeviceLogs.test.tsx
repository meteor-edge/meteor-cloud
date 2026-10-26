import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeviceLogs } from "@/components/fleet/DeviceLogs";
import { groupSources } from "@/lib/logSources";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return { ...actual, createLogSession: vi.fn(), getLogSources: vi.fn() };
});

import { createLogSession, getLogSources } from "@/api/fleet";

class FakeSocket {
  static OPEN = 1;
  static last: FakeSocket | null = null;
  readyState = 0;
  binaryType = "blob";
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.last = this;
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {}
}

function renderLogs() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeviceLogs token="t" organizationId="org-1" deviceId="dev-1" deviceName="pi" />
    </QueryClientProvider>,
  );
}

describe("log source helpers", () => {
  it("groups deployed workloads first, including compose containers", () => {
    const groups = groupSources(
      [
        { kind: "journal", name: "" },
        { kind: "systemd", name: "nginx.service", state: "active" },
        { kind: "systemd", name: "app.service", state: "active" },
        { kind: "docker", name: "shop-web-1", project: "shop" },
        { kind: "docker", name: "other" },
        { kind: "file", name: "/var/log/nginx/access.log" },
      ],
      ["systemd:app.service", "compose:shop"],
    );
    expect(groups.map((g) => [g.title, g.sources.map((s) => s.name)])).toEqual([
      ["Deployed by MeteorCloud", ["app.service", "shop-web-1"]],
      ["Containers", ["other"]],
      ["Services", ["nginx.service"]],
      ["Log files", ["/var/log/nginx/access.log"]],
      ["System", [""]],
    ]);
  });
});

describe("DeviceLogs", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("opens a log session for the picked source and shows its lines", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.mocked(getLogSources).mockResolvedValue({
      sources: [
        { kind: "journal", name: "" },
        { kind: "systemd", name: "nginx.service", state: "active" },
      ],
      deployed: [],
    });
    vi.mocked(createLogSession).mockResolvedValue({
      session_id: "s-1",
      ticket: "sh_secret",
      websocket_path: "/api/v1/log-sessions/s-1/ws",
      expires_at: "2026-10-26T10:05:00Z",
      device_id: "dev-1",
    });

    renderLogs();
    fireEvent.click(await screen.findByRole("button", { name: /nginx\.service/ }));
    await waitFor(() => expect(FakeSocket.last).not.toBeNull());
    expect(createLogSession).toHaveBeenCalledWith("t", "org-1", "dev-1", {
      kind: "systemd",
      name: "nginx.service",
      lines: 500,
      since: null,
      grep: null,
      follow: true,
    });

    const socket = FakeSocket.last!;
    expect(socket.url).toMatch(/\/api\/v1\/log-sessions\/s-1\/ws$/);
    expect(socket.url).not.toContain("sh_secret");
    act(() => {
      socket.readyState = FakeSocket.OPEN;
      socket.onopen?.();
    });
    expect(socket.sent[0]).toBe(JSON.stringify({ type: "auth", ticket: "sh_secret" }));

    act(() => {
      socket.onmessage?.({ data: "[end of log]" });
      socket.onclose?.();
    });
    expect(await screen.findByText("end of log")).toBeInTheDocument();

    // A device-side search opens a new session with grep.
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "error" } });
    fireEvent.click(screen.getByRole("button", { name: "Run search" }));
    await waitFor(() =>
      expect(createLogSession).toHaveBeenLastCalledWith(
        "t",
        "org-1",
        "dev-1",
        expect.objectContaining({ grep: "error" }),
      ),
    );
  });

  it("shows why the device could not list its logs", async () => {
    const { ApiError } = await import("@/api/http");
    vi.mocked(getLogSources).mockRejectedValue(
      new ApiError(409, "log_sources_unavailable", "The device did not list its logs."),
    );
    renderLogs();
    expect(await screen.findByText("The device did not list its logs.")).toBeInTheDocument();
  });
});
