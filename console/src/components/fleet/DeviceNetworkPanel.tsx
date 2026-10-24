import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  getDeviceConnectivity,
  reenrollDeviceConnectivity,
  revokeDeviceConnectivity,
  type DeviceConnectivity,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/utils";

type DeviceNetworkPanelProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  /** Revoke and re-enroll need device.update. */
  canManage?: boolean;
  /** The SSH command is shown only to members who may open a device shell. */
  canShell?: boolean;
};

const REFRESH_MS = 15_000;

type State = NonNullable<DeviceConnectivity["state"]>;

const STATES: Record<State, { label: string; dot: string; hint: string }> = {
  connected: { label: "Connected", dot: "bg-green-500", hint: "" },
  reconnecting: {
    label: "Reconnecting",
    dot: "bg-amber-500",
    hint: "The agent is checking in, but the tunnel has no recent handshake. It reconnects on its own.",
  },
  offline: {
    label: "Offline",
    dot: "bg-muted-foreground",
    hint: "Neither the tunnel nor the agent has been heard from recently.",
  },
  pending: {
    label: "Waiting for the device",
    dot: "bg-muted-foreground",
    hint: "The agent registers its WireGuard key on its next check.",
  },
  revoked: {
    label: "Revoked",
    dot: "bg-destructive",
    hint: "The device cannot reconnect until it is re-enrolled.",
  },
};

/** Older servers send only `status` and `connected`. */
function stateOf(net: DeviceConnectivity): State {
  if (net.state) return net.state;
  if (net.status === "revoked") return "revoked";
  if (net.status !== "active") return "pending";
  return net.connected ? "connected" : "pending";
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  );
}

function when(value: string | null | undefined) {
  return value ? formatDateTime(value) : "Never";
}

/** The device's private WireGuard address, its tunnel health, and how to SSH to it over the tunnel. */
export function DeviceNetworkPanel({
  token,
  organizationId,
  deviceId,
  canManage = false,
  canShell = false,
}: DeviceNetworkPanelProps) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const queryKey = ["device-connectivity", organizationId, deviceId, token];
  const query = useQuery({
    queryKey,
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
  const state = stateOf(net);
  const shown = STATES[state];
  const revoked = state === "revoked";
  const port = net.ssh_port !== 22 ? ` -p ${net.ssh_port}` : "";
  const sshCommand = net.wg_address
    ? `meteorcloud-ssh${port} ${net.ssh_user}@${net.wg_address}`
    : "";

  async function copy() {
    await navigator.clipboard.writeText(sshCommand);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  async function run(action: "revoke" | "reenroll") {
    const question =
      action === "revoke"
        ? "Revoke this device's WireGuard access? Browser SSH and its app addresses stop working until it is re-enrolled."
        : "Re-enroll this device? Its WireGuard key, address and pinned SSH host key are forgotten; the agent registers again on its own.";
    if (!window.confirm(question)) return;
    setBusy(true);
    setActionError(null);
    try {
      const call = action === "revoke" ? revokeDeviceConnectivity : reenrollDeviceConnectivity;
      queryClient.setQueryData(queryKey, await call(token, organizationId, deviceId));
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not update WireGuard access.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Internal IP">
          <span className="font-mono">{net.wg_address ?? "—"}</span>
        </Field>
        <Field label="Status">
          <span className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${shown.dot}`} />
            {shown.label}
          </span>
        </Field>
        <Field label="Last handshake">{when(net.last_handshake_at)}</Field>
        {net.agent_last_seen_at !== undefined && (
          <Field label="Agent last check-in">{when(net.agent_last_seen_at)}</Field>
        )}
        {net.ssh_host_key_fingerprint !== undefined && (
          <Field label="SSH host key">
            <span className="break-all font-mono text-xs">
              {net.ssh_host_key_fingerprint ?? "Not reported (trusted on first use)"}
            </span>
          </Field>
        )}
        {net.ssh_auth !== undefined && (
          <Field label="SSH auth">
            {net.ssh_auth === "ca" ? "Certificate (per session)" : "Shared gateway key"}
          </Field>
        )}
        {net.last_ssh_ok_at !== undefined && (
          <Field label="Last SSH session">{when(net.last_ssh_ok_at)}</Field>
        )}
      </div>
      {shown.hint && <p className="text-sm text-muted-foreground">{shown.hint}</p>}
      {net.last_ssh_error && (
        <p className="text-sm text-destructive">
          Last SSH error{net.last_ssh_error_at ? ` (${formatDateTime(net.last_ssh_error_at)})` : ""}
          : {net.last_ssh_error}
        </p>
      )}

      {canShell && sshCommand && !revoked && (
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
            Run it on the MeteorCloud server ({net.server_address} inside the tunnel); no sudo.
            Devices cannot reach each other.
          </p>
        </div>
      )}

      {canManage && (
        <div className="flex flex-wrap items-center gap-2">
          {!revoked && net.status === "active" && (
            <Button
              variant="outline"
              size="sm"
              className="text-destructive"
              disabled={busy}
              onClick={() => run("revoke")}
            >
              Revoke access
            </Button>
          )}
          {state !== "pending" && (
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => run("reenroll")}>
              Re-enroll
            </Button>
          )}
          {actionError && <p className="text-sm text-destructive">{actionError}</p>}
        </div>
      )}
    </div>
  );
}
