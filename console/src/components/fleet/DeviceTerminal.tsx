import { useEffect, useRef, useState } from "react";

import { createShellSession } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { resolveApiBaseUrl } from "@/lib/apiBase";
import { Button } from "@/components/ui/button";

type DeviceTerminalProps = {
  token: string;
  organizationId: string;
  deviceId: string;
};

function websocketUrl(path: string, ticket: string): string {
  const api = resolveApiBaseUrl();
  const wsBase = api.replace(/^http/, "ws");
  return `${wsBase}${path}?ticket=${encodeURIComponent(ticket)}`;
}

export function DeviceTerminal({ token, organizationId, deviceId }: DeviceTerminalProps) {
  const preRef = useRef<HTMLPreElement | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [output, setOutput] = useState("Click Connect to open a shell over WireGuard.\n");
  const [command, setCommand] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    return () => {
      socketRef.current?.close();
    };
  }, []);

  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [output]);

  async function connect() {
    setError(null);
    try {
      const session = await createShellSession(token, organizationId, deviceId);
      const socket = new WebSocket(websocketUrl(session.websocket_path, session.ticket));
      socket.binaryType = "arraybuffer";
      socket.onmessage = (event) => {
        const text =
          typeof event.data === "string"
            ? event.data
            : new TextDecoder().decode(event.data as ArrayBuffer);
        setOutput((current) => current + text);
      };
      socket.onclose = () => setConnected(false);
      socket.onopen = () => setConnected(true);
      socketRef.current = socket;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open a shell session.");
    }
  }

  function send() {
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      return;
    }
    socketRef.current.send(command.endsWith("\n") ? command : `${command}\n`);
    setCommand("");
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Browser SSH through MeteorCloud. The device is reached on its WireGuard address;
        SSH is never exposed on the public internet.
      </p>
      <pre
        ref={preRef}
        className="h-64 overflow-auto rounded-md bg-background p-3 font-mono text-xs text-foreground"
      >
        {output}
      </pre>
      <div className="flex flex-wrap gap-2">
        <Button type="button" onClick={() => void connect()} disabled={connected}>
          {connected ? "Connected" : "Connect"}
        </Button>
        <input
          className="h-10 min-w-[12rem] flex-1 rounded-md border border-input bg-field px-3 font-mono text-sm"
          value={command}
          disabled={!connected}
          onChange={(event) => setCommand(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              send();
            }
          }}
          placeholder={connected ? "Type a command" : "Connect first"}
        />
        <Button type="button" variant="outline" disabled={!connected} onClick={send}>
          Send
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
