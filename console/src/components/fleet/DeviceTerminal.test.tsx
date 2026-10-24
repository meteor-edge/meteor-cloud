import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeviceTerminal } from "@/components/fleet/DeviceTerminal";

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return { ...actual, createShellSession: vi.fn() };
});

import { createShellSession } from "@/api/fleet";

class FakeSocket {
  static OPEN = 1;
  static last: FakeSocket | null = null;
  readyState = 0;
  binaryType = "blob";
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
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

describe("DeviceTerminal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the ticket in the first frame, not the URL", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.mocked(createShellSession).mockResolvedValue({
      session_id: "s-1",
      ticket: "sh_secret",
      websocket_path: "/api/v1/shell-sessions/s-1/ws",
      expires_at: "2026-10-24T10:00:00Z",
      device_id: "dev-1",
    });

    render(<DeviceTerminal token="t" organizationId="org-1" deviceId="dev-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(FakeSocket.last).not.toBeNull());

    const socket = FakeSocket.last!;
    expect(socket.url).toMatch(/\/api\/v1\/shell-sessions\/s-1\/ws$/);
    expect(socket.url).not.toContain("sh_secret");
    act(() => {
      socket.readyState = FakeSocket.OPEN;
      socket.onopen?.();
    });
    expect(socket.sent[0]).toBe(JSON.stringify({ type: "auth", ticket: "sh_secret" }));
  });
});
