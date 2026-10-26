import { useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Loader2, Power, TerminalSquare } from "lucide-react";

import { createShellSession } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { websocketUrl } from "@/lib/apiBase";
import { Button } from "@/components/ui/button";

type DeviceTerminalProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  deviceName?: string;
};

type Status = "idle" | "connecting" | "connected" | "closed";

export function DeviceTerminal({
  token,
  organizationId,
  deviceId,
  deviceName,
}: DeviceTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);

  // One xterm instance for the lifetime of the panel; every keystroke goes straight to the
  // device's PTY (ssh -tt), so line editing, Tab completion and full-screen programs work.
  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      scrollback: 5000,
      theme: {
        background: "#000000",
        foreground: "#e5e5e5",
        cursor: "#22c55e",
        selectionBackground: "#3f3f46",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    term.onData((data) => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(data);
      }
    });
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(containerRef.current);
    termRef.current = term;
    fitRef.current = fit;
    return () => {
      observer.disconnect();
      socketRef.current?.close();
      term.dispose();
    };
  }, []);

  async function connect() {
    const term = termRef.current;
    if (!term) {
      return;
    }
    setError(null);
    setStatus("connecting");
    term.reset();
    try {
      const session = await createShellSession(token, organizationId, deviceId);
      // The ticket goes in the first frame, not the URL, so it never reaches access logs.
      const socket = new WebSocket(websocketUrl(session.websocket_path));
      socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        socket.send(JSON.stringify({ type: "auth", ticket: session.ticket }));
        setStatus("connected");
        fitRef.current?.fit();
        term.focus();
      };
      socket.onmessage = (event) => {
        term.write(
          typeof event.data === "string" ? event.data : new Uint8Array(event.data as ArrayBuffer),
        );
      };
      socket.onerror = () => setError("The connection to the device failed.");
      socket.onclose = () => {
        socketRef.current = null;
        setStatus("closed");
      };
      socketRef.current = socket;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open a shell session.");
      setStatus("idle");
    }
  }

  function disconnect() {
    socketRef.current?.close();
  }

  const title = deviceName ?? "device";

  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-lg border border-zinc-800 bg-black shadow-lg">
        <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900 px-3 py-2">
          <div className="flex items-center gap-2 font-mono text-xs text-zinc-300">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                status === "connected"
                  ? "bg-green-500"
                  : status === "connecting"
                    ? "bg-yellow-400"
                    : "bg-zinc-600"
              }`}
              aria-hidden
            />
            <span>{title}</span>
            <span className="text-zinc-500">
              {status === "connected"
                ? "connected over WireGuard"
                : status === "connecting"
                  ? "connecting…"
                  : "not connected"}
            </span>
          </div>
          {status === "connected" && (
            <button
              type="button"
              onClick={disconnect}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
            >
              <Power className="h-3.5 w-3.5" /> Disconnect
            </button>
          )}
        </div>

        <div className="relative h-[28rem]">
          <div ref={containerRef} className="absolute inset-0 p-2" />

          {status !== "connected" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-black/85 text-center">
              <TerminalSquare className="h-10 w-10 text-zinc-500" aria-hidden />
              <div className="space-y-1">
                <p className="font-mono text-sm text-zinc-200">
                  {status === "closed" ? "Session ended" : `Open a shell on ${title}`}
                </p>
                <p className="max-w-sm text-xs text-zinc-500">
                  Browser SSH over the device&apos;s WireGuard tunnel. SSH is never exposed on the
                  public internet.
                </p>
              </div>
              <Button
                type="button"
                onClick={() => void connect()}
                disabled={status === "connecting"}
              >
                {status === "connecting" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Connecting…
                  </>
                ) : status === "closed" ? (
                  "Reconnect"
                ) : (
                  "Connect"
                )}
              </Button>
              {error && <p className="text-xs text-red-400">{error}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
