import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeviceLogs } from "@/components/fleet/DeviceLogs";
import { MAX_BUFFER_LINES, appendLines, buildMatcher, groupSources } from "@/lib/logSources";

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

  it("keeps partial lines, strips colors and caps the buffer", () => {
    const buffer: string[] = [];
    let rest = appendLines(buffer, "", "one\r\ntw");
    rest = appendLines(buffer, rest, "o \x1b[31mred\x1b[0m\nthree");
    expect(buffer).toEqual(["one", "two red"]);
    expect(rest).toBe("three");
    const big = Array.from({ length: MAX_BUFFER_LINES + 5 }, (_, i) => `l${i}`).join("\n") + "\n";
    const capped: string[] = [];
    appendLines(capped, "", big);
    expect(capped.length).toBe(MAX_BUFFER_LINES);
    expect(capped[0]).toBe("l5");
  });

  it("filters by text or /regex/ and ignores bad patterns", () => {
    expect(buildMatcher("")).toBeNull();
    expect(buildMatcher("a.b")?.test("axb")).toBe(false);
    expect(buildMatcher("/err(or)?/")?.test("ERR")).toBe(true);
    expect(buildMatcher("/[/")).toBeNull();
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
      reported_at: "2026-10-26T10:00:00Z",
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
      socket.onmessage?.({
        data: new TextEncoder().encode("GET / 200\nGET /x 500 error\n").buffer,
      });
    });
    await waitFor(() => expect(screen.getByText("GET / 200")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Filter lines"), { target: { value: "error" } });
    await waitFor(() => expect(screen.queryByText("GET / 200")).not.toBeInTheDocument());
    expect(screen.getByText("error").tagName).toBe("MARK");
  });

  it("explains how to enable logs when the agent never reported sources", async () => {
    vi.mocked(getLogSources).mockResolvedValue({ sources: [], reported_at: null, deployed: [] });
    renderLogs();
    expect(await screen.findByText(/has not reported any log sources/)).toBeInTheDocument();
  });
});
