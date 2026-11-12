import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getDeviceWifi,
  removeDeviceWifiNetwork,
  setDeviceWifiNetwork,
  type DeviceWifi,
  type DeviceWifiNetwork,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/lib/utils";

type Props = {
  token: string;
  organizationId: string;
  deviceId: string;
  /** device.network: owners and admins by default. Others see the state but cannot change it. */
  canChange: boolean;
};

type Row = {
  ssid: string;
  /** On the device, as it last reported. */
  saved: boolean;
  connected: boolean;
  /** The latest change ordered from this console, if any. */
  change: DeviceWifiNetwork | null;
};

function rowsOf(wifi: DeviceWifi): Row[] {
  const rows = new Map<string, Row>();
  for (const saved of wifi.status?.saved_networks ?? []) {
    rows.set(saved.ssid, { ssid: saved.ssid, saved: true, connected: false, change: null });
  }
  for (const change of wifi.networks) {
    const row = rows.get(change.ssid) ?? {
      ssid: change.ssid,
      saved: false,
      connected: false,
      change: null,
    };
    rows.set(change.ssid, { ...row, change });
  }
  const connected = wifi.status?.connected_ssid;
  if (connected) {
    const row = rows.get(connected) ?? {
      ssid: connected,
      saved: true,
      connected: true,
      change: null,
    };
    rows.set(connected, { ...row, connected: true });
  }
  return [...rows.values()].sort((a, b) => a.ssid.localeCompare(b.ssid));
}

function changeNote(change: DeviceWifiNetwork): { text: string; tone: string } | null {
  if (change.state === "pending") {
    return {
      text:
        change.action === "remove"
          ? "Removal is waiting for the device."
          : "Waiting for the device to pick up the change (its next heartbeat).",
      tone: "text-muted-foreground",
    };
  }
  if (change.state === "rolled_back") {
    return {
      text: change.last_error ?? "The device went back to its old Wi-Fi settings.",
      tone: "text-amber-500",
    };
  }
  if (change.state === "failed") {
    return {
      text: change.last_error ?? "The device could not make this change.",
      tone: "text-destructive",
    };
  }
  if (change.reported_at) {
    return {
      text: `Changed on the device ${formatDateTime(change.reported_at)}.`,
      tone: "text-muted-foreground",
    };
  }
  return null;
}

/** Wi-Fi networks of one device: what it reports, and changes ordered from here. */
export function DeviceWifiPanel({ token, organizationId, deviceId, canChange }: Props) {
  const queryClient = useQueryClient();
  const queryKey = ["device-wifi", organizationId, deviceId];
  const [editing, setEditing] = useState<string | null>(null); // "" = a new network
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [open, setOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [connectNow, setConnectNow] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const wifiQuery = useQuery({
    queryKey,
    queryFn: () => getDeviceWifi(token, organizationId, deviceId),
    // A change is settled by the device within a heartbeat or two: keep looking while one is open.
    refetchInterval: (query) =>
      query.state.data?.networks.some((network) => network.state === "pending") ? 5000 : 30000,
  });

  const onError = (reason: unknown) =>
    setError(reason instanceof ApiError ? reason.message : "The change could not be saved.");
  const onDone = (wifi: DeviceWifi) => {
    queryClient.setQueryData(queryKey, wifi);
    setEditing(null);
    setPassword("");
    setError(null);
  };
  const saveMutation = useMutation({
    mutationFn: () =>
      setDeviceWifiNetwork(token, organizationId, deviceId, {
        ssid,
        security: open ? "open" : "wpa-psk",
        password: open ? undefined : password,
        hidden,
        connect_now: connectNow,
      }),
    onSuccess: onDone,
    onError,
  });
  const removeMutation = useMutation({
    mutationFn: (name: string) => removeDeviceWifiNetwork(token, organizationId, deviceId, name),
    onSuccess: onDone,
    onError,
  });

  const startEditing = (name: string) => {
    setEditing(name);
    setSsid(name);
    setPassword("");
    setOpen(false);
    setHidden(false);
    setConnectNow(true);
    setError(null);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!open && (password.length < 8 || password.length > 63)) {
      setError("The Wi-Fi password must be 8 to 63 characters.");
      return;
    }
    saveMutation.mutate();
  };

  const onRemove = (name: string) => {
    if (window.confirm(`Remove "${name}" from this device? It will no longer connect to it.`)) {
      removeMutation.mutate(name);
    }
  };

  const wifi = wifiQuery.data;
  const status = wifi?.status ?? null;
  const rows = wifi ? rowsOf(wifi) : [];
  const usable = status?.supported === true;

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow">
      <div>
        <h2 className="text-lg font-semibold">Wi-Fi</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Add a network or change a password without touching the device. The password is sent
          encrypted, kept only until the device has it, and never shown again.
        </p>
      </div>

      {wifiQuery.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {wifiQuery.isError && (
        <p className="text-sm text-destructive">The Wi-Fi settings could not be loaded.</p>
      )}

      {wifi && status === null && (
        <p className="text-sm text-muted-foreground">
          This device has not reported its Wi-Fi yet. It needs meteorcli 0.3.0 or newer and has to
          be online. Changes you order here wait for it.
        </p>
      )}
      {status && !status.supported && (
        <p className="text-sm text-amber-500">
          {status.unsupported_reason ?? "Wi-Fi cannot be managed on this device."}
        </p>
      )}

      {status?.supported && (
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Connected to
            </p>
            <p className="mt-1 text-sm">
              {status.connected_ssid
                ? `${status.connected_ssid}${status.signal_percent != null ? ` (signal ${status.signal_percent}%)` : ""}`
                : "No Wi-Fi network"}
            </p>
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Cable
            </p>
            <p className="mt-1 text-sm">{status.wired_connected ? "Connected" : "Not connected"}</p>
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Reported
            </p>
            <p className="mt-1 text-sm">{formatDateTime(status.reported_at)}</p>
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <ul className="divide-y divide-border rounded-md border border-border">
          {rows.map((row) => {
            const note = row.change ? changeNote(row.change) : null;
            return (
              <li key={row.ssid} className="flex flex-wrap items-center justify-between gap-3 p-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">
                    {row.ssid}
                    {row.connected && (
                      <span className="ml-2 rounded bg-green-500/15 px-1.5 py-0.5 text-xs text-green-500">
                        In use
                      </span>
                    )}
                  </p>
                  {note && <p className={`mt-1 text-xs ${note.tone}`}>{note.text}</p>}
                </div>
                {canChange && (
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => startEditing(row.ssid)}>
                      Change password
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={removeMutation.isPending}
                      onClick={() => onRemove(row.ssid)}
                    >
                      Remove
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {!canChange && wifi && (
        <p className="text-sm text-muted-foreground">
          Only owners and admins can change a device's Wi-Fi.
        </p>
      )}

      {canChange && wifi && editing === null && (
        <Button variant="outline" onClick={() => startEditing("")}>
          Add a network
        </Button>
      )}

      {canChange && editing !== null && (
        <form onSubmit={onSubmit} className="space-y-3 rounded-md border border-border p-4">
          <h3 className="text-sm font-semibold">
            {editing ? `Change the password of "${editing}"` : "Add a network"}
          </h3>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label htmlFor="wifi-ssid">Network name (SSID)</Label>
              <Input
                id="wifi-ssid"
                value={ssid}
                maxLength={32}
                required
                disabled={editing !== ""}
                onChange={(event) => setSsid(event.target.value)}
              />
            </div>
            {!open && (
              <div>
                <Label htmlFor="wifi-password">Wi-Fi password</Label>
                <Input
                  id="wifi-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
            )}
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={open}
              onChange={(event) => setOpen(event.target.checked)}
            />
            Open network (no password)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={hidden}
              onChange={(event) => setHidden(event.target.checked)}
            />
            Hidden network
          </label>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">When to use it</legend>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="wifi-when"
                className="mt-1"
                checked={connectNow}
                onChange={() => setConnectNow(true)}
              />
              <span>
                Connect now and check
                <span className="block text-xs text-muted-foreground">
                  The device switches to this network. If it cannot join it, or cannot reach
                  MeteorCloud through it within 90 seconds, it goes back to its old settings.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="wifi-when"
                className="mt-1"
                checked={!connectNow}
                onChange={() => setConnectNow(false)}
              />
              <span>
                Save for later
                <span className="block text-xs text-muted-foreground">
                  The device stays on its current connection and uses this the next time it sees the
                  network. Choose this before you change the router's password. It is not tested.
                </span>
              </span>
            </label>
          </fieldset>
          {status && !usable && (
            <p className="text-xs text-amber-500">
              This device reports that its Wi-Fi cannot be managed, so the change will probably
              fail.
            </p>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex gap-2">
            <Button type="submit" disabled={saveMutation.isPending}>
              {saveMutation.isPending ? "Sending…" : "Send to device"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {error && editing === null && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
