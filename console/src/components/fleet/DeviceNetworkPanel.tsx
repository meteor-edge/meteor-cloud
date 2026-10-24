import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getDeviceConnectivity } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/utils";

type DeviceNetworkPanelProps = {
  token: string;
  organizationId: string;
  deviceId: string;
};

const REFRESH_MS = 15_000;

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  );
}

/** The device's private WireGuard address and how to SSH to it over the tunnel. */
export function DeviceNetworkPanel({ token, organizationId, deviceId }: DeviceNetworkPanelProps) {
  const [copied, setCopied] = useState(false);
  const query = useQuery({
    queryKey: ["device-connectivity", organizationId, deviceId, token],
    queryFn: () => getDeviceConnectivity(token, organizationId, deviceId),
    refetchInterval: REFRESH_MS,
    retry: false,
  });

  if (query.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (query.error) {
    const code = query.error instanceof ApiError ? query.error.code : "";
    return (
      <p className="text-sm text-muted-foreground">
        {code === "connectivity_not_found"
          ? "This device has not joined the WireGuard network yet. The agent joins it automatically when it starts."
          : code === "connectivity_disabled"
            ? "WireGuard is turned off on this server."
            : "Could not load the WireGuard status."}
      </p>
    );
  }

  const net = query.data!;
  const revoked = net.status !== "active";
  const port = net.ssh_port !== 22 ? ` -p ${net.ssh_port}` : "";
  const sshCommand = net.wg_address
    ? `meteorcloud-ssh${port} ${net.ssh_user}@${net.wg_address}`
    : "";

  async function copy() {
    await navigator.clipboard.writeText(sshCommand);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Internal IP">
          <span className="font-mono">{net.wg_address ?? "—"}</span>
        </Field>
        <Field label="Status">
          <span className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                revoked ? "bg-destructive" : net.connected ? "bg-green-500" : "bg-muted-foreground"
              }`}
            />
            {revoked ? "Revoked" : net.connected ? "Connected" : "Waiting for the device"}
          </span>
        </Field>
        <Field label="Last handshake">
          {net.last_handshake_at ? formatDateTime(net.last_handshake_at) : "Never"}
        </Field>
      </div>

      {sshCommand && !revoked && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            SSH over WireGuard
          </p>
          <div className="flex items-center gap-2">
            <pre className="flex-1 overflow-x-auto rounded-md bg-black p-3 font-mono text-sm text-green-400">
              {sshCommand}
            </pre>
            <Button variant="secondary" size="sm" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Run it on the MeteorCloud server ({net.server_address} inside the tunnel). Same key as
            the browser terminal; no sudo. Devices cannot reach each other.
          </p>
        </div>
      )}
    </div>
  );
}
