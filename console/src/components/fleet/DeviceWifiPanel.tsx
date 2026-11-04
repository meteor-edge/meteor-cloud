import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  deleteDeviceWifi,
  getDeviceWifi,
  putDeviceWifi,
  type DeviceWifiSettings,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type DeviceNetworkLink = {
  name: string;
  type: string;
  state: string;
  connection?: string | null;
};

type DeviceWifiPanelProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  canManage?: boolean;
  /** Last heartbeat snapshot of ethernet/wifi links (from the device record). */
  networkLinks?: DeviceNetworkLink[] | null;
};

function syncLabel(row: DeviceWifiSettings | null | undefined) {
  if (!row) return "Not managed";
  if (row.sync_status === "in_sync") return "Applied";
  if (row.sync_status === "pending") return "Pending on device";
  if (row.sync_status === "failed") return "Failed";
  if (row.sync_status === "cleared") return "Cleared";
  return row.sync_status ?? "—";
}

/** Post-enroll WiFi desired state. PSK is write-only in the console. */
function linkLabel(link: DeviceNetworkLink) {
  const kind = link.type === "wifi" ? "WiFi" : link.type === "ethernet" ? "Ethernet" : link.type;
  const conn = link.connection ? ` · ${link.connection}` : "";
  return `${link.name} (${kind}) — ${link.state}${conn}`;
}

export function DeviceWifiPanel({
  token,
  organizationId,
  deviceId,
  canManage = false,
  networkLinks = null,
}: DeviceWifiPanelProps) {
  const queryClient = useQueryClient();
  const [ssid, setSsid] = useState("");
  const [psk, setPsk] = useState("");
  const [iface, setIface] = useState("wlan0");
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["device-wifi", organizationId, deviceId, token],
    queryFn: () => getDeviceWifi(token, organizationId, deviceId),
    enabled: Boolean(token && organizationId && deviceId),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    const row = query.data;
    if (!row) return;
    setSsid(row.ssid);
    setIface(row.interface);
    setHidden(row.hidden);
  }, [query.data]);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["device-wifi", organizationId, deviceId] });

  const saveMutation = useMutation({
    mutationFn: () =>
      putDeviceWifi(token, organizationId, deviceId, {
        ssid: ssid.trim(),
        psk,
        interface: iface.trim() || "wlan0",
        hidden,
      }),
    onSuccess: () => {
      setPsk("");
      setError(null);
      setMessage("Saved. The device will apply WiFi on its next check-in.");
      invalidate();
    },
    onError: (err: unknown) => {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : "Could not save WiFi settings.");
    },
  });

  const clearMutation = useMutation({
    mutationFn: () => deleteDeviceWifi(token, organizationId, deviceId),
    onSuccess: () => {
      setSsid("");
      setPsk("");
      setHidden(false);
      setError(null);
      setMessage("Cloud WiFi management cleared for this device.");
      invalidate();
    },
    onError: (err: unknown) => {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : "Could not clear WiFi settings.");
    },
  });

  const row = query.data;

  const links = networkLinks?.filter((link) => link.type === "ethernet" || link.type === "wifi") ?? [];

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Set WiFi for an already enrolled device. First boot still uses Raspberry Pi Imager or
        cloud-init. Prefer Ethernet while changing WiFi so the agent can report success or revert.
      </p>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Connected interfaces
        </p>
        {links.length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">
            No link report yet (needs a recent agent heartbeat).
          </p>
        ) : (
          <ul className="mt-1 space-y-1 text-sm">
            {links.map((link) => (
              <li key={`${link.type}:${link.name}`}>{linkLabel(link)}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Cloud WiFi status
          </p>
          <p className="mt-1 text-sm">{syncLabel(row)}</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Applied SSID
          </p>
          <p className="mt-1 text-sm">{row?.applied_ssid || "—"}</p>
        </div>
      </div>
      {row?.sync_error && (
        <p className="text-sm text-destructive">{row.sync_error}</p>
      )}

      {canManage ? (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            setError(null);
            setMessage(null);
            saveMutation.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="wifi-ssid">SSID</Label>
            <Input
              id="wifi-ssid"
              value={ssid}
              onChange={(event) => setSsid(event.target.value)}
              maxLength={32}
              required
              autoComplete="off"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="wifi-psk">Password (PSK)</Label>
            <Input
              id="wifi-psk"
              type="password"
              value={psk}
              onChange={(event) => setPsk(event.target.value)}
              minLength={8}
              maxLength={63}
              required
              autoComplete="new-password"
              placeholder={row?.psk_set ? "Enter a new password to rotate" : undefined}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="wifi-iface">Interface</Label>
            <Input
              id="wifi-iface"
              value={iface}
              onChange={(event) => setIface(event.target.value)}
              maxLength={32}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={hidden}
              onChange={(event) => setHidden(event.target.checked)}
            />
            Hidden network
          </label>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={saveMutation.isPending || psk.length < 8}>
              {saveMutation.isPending ? "Saving…" : "Save WiFi"}
            </Button>
            {row && (
              <Button
                type="button"
                variant="secondary"
                disabled={clearMutation.isPending}
                onClick={() => {
                  if (window.confirm("Stop managing WiFi from the cloud for this device?")) {
                    clearMutation.mutate();
                  }
                }}
              >
                Clear
              </Button>
            )}
          </div>
        </form>
      ) : (
        <p className="text-sm text-muted-foreground">
          {row ? `Configured SSID: ${row.ssid}` : "No cloud-managed WiFi on this device."}
        </p>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}
      {message && <p className="text-sm text-muted-foreground">{message}</p>}
    </div>
  );
}
