import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  deleteDevice,
  getDevice,
  listDeviceGroups,
  listDeviceTypes,
  pingDevice,
  revokeDeviceCredential,
  rotateDeviceCredential,
  setDeviceEnabled,
  updateDevice,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { MqttConsole } from "@/components/fleet/MqttConsole";
import { OneTimeSecretDialog } from "@/components/fleet/OneTimeSecretDialog";
import { StatusBadge } from "@/components/fleet/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { canManageFleet } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

function Detail({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm">{value || "—"}</p>
    </div>
  );
}

function metricText(metrics: Record<string, unknown> | null, key: string): string | null {
  if (!metrics || metrics[key] == null) {
    return null;
  }
  const value = metrics[key];
  return typeof value === "number" ? String(value) : String(value);
}

export function DeviceDetailPage() {
  const { organizationId = "", deviceId = "" } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [rotatedToken, setRotatedToken] = useState<string | null>(null);
  const [pingResult, setPingResult] = useState<string | null>(null);
  const [pinging, setPinging] = useState(false);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const deviceQuery = useQuery({
    queryKey: ["device", organizationId, deviceId, token],
    queryFn: () => getDevice(token!, organizationId, deviceId),
    enabled: Boolean(token && organizationId && deviceId),
  });
  const typesQuery = useQuery({
    queryKey: ["device-types", organizationId, token],
    queryFn: () => listDeviceTypes(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageFleet(orgQuery.data?.current_user_role);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["device", organizationId, deviceId] });

  const enabledMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      setDeviceEnabled(token!, organizationId, deviceId, enabled),
    onSuccess: invalidate,
    onError: (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "Could not update device."),
  });

  const rotateMutation = useMutation({
    mutationFn: () => rotateDeviceCredential(token!, organizationId, deviceId),
    onSuccess: async (credential) => {
      setRotatedToken(credential.token);
      await invalidate();
    },
    onError: (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "Could not rotate credential."),
  });

  if (deviceQuery.isLoading) {
    return <p className="text-muted-foreground">Loading device…</p>;
  }
  if (!deviceQuery.data) {
    return <p className="text-red-700">Device was not found.</p>;
  }
  const device = deviceQuery.data;
  const currentName = name ?? device.name;
  const commandsTopic = `devices/${device.id}/commands`;
  const statusTopic = `devices/${device.id}/status`;
  const metricsTopic = `devices/${device.id}/metrics`;
  const meteorcliExamples = [
    "meteorcli mqtt-test",
    `meteorcli mqtt-test ${statusTopic} '{"status":"online","mac_address":"${device.mac_addresses[0] ?? ""}"}'`,
    "meteorcli mqtt-listen",
    `meteorcli mqtt-listen commands`,
  ].join("\n");

  async function onSave() {
    setError(null);
    try {
      await updateDevice(token!, organizationId, deviceId, { name: currentName });
      setName(null);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update device.");
    }
  }

  async function onAssign(field: "device_type_id" | "device_group_id", value: string) {
    setError(null);
    const clearKey = field === "device_type_id" ? "clear_device_type" : "clear_device_group";
    try {
      await updateDevice(
        token!,
        organizationId,
        deviceId,
        value ? { [field]: value } : { [clearKey]: true },
      );
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update assignment.");
    }
  }

  async function onRevokeCredential() {
    if (!window.confirm("Revoke this device's credential? It will need to re-register.")) {
      return;
    }
    setError(null);
    try {
      await revokeDeviceCredential(token!, organizationId, deviceId);
      await invalidate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not revoke credential.");
    }
  }

  async function onDelete() {
    if (!window.confirm(`Delete device "${device.name}"? This cannot be undone.`)) {
      return;
    }
    setError(null);
    try {
      await deleteDevice(token!, organizationId, deviceId);
      await queryClient.invalidateQueries({ queryKey: ["devices", organizationId] });
      navigate(`/organizations/${organizationId}/devices`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the device.");
    }
  }

  async function onTestConnection() {
    setError(null);
    setPingResult(null);
    setPinging(true);
    try {
      const result = await pingDevice(token!, organizationId, deviceId);
      if (result.status === "completed") {
        setPingResult(
          `Connection test successful${
            result.round_trip_ms != null ? `. Round trip: ${result.round_trip_ms} ms` : ""
          }`,
        );
        await invalidate();
      } else {
        setError(result.message || "Connection test failed.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Connection test failed.");
    } finally {
      setPinging(false);
    }
  }

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-semibold tracking-tight">{device.name}</h1>
            <StatusBadge status={device.status} />
          </div>
          <p className="mt-2 text-muted-foreground">{device.hostname ?? device.mac_addresses[0] ?? "—"}</p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}/devices`}>Back to devices</Link>
        </Button>
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}

      <div className="space-y-3 rounded-lg border border-border bg-white/80 p-6 shadow-sm">
        <h2 className="text-lg font-semibold">MQTT Connection</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          <Detail label="MQTT configured" value={device.mqtt_configured ? "yes" : "no"} />
          <Detail
            label="Last MQTT status"
            value={device.mqtt_status ?? "unknown"}
          />
          <Detail
            label="Last status timestamp"
            value={device.mqtt_status_at ? formatDateTime(device.mqtt_status_at) : "—"}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Detail label="Device ID" value={device.id} />
          <Detail
            label="MAC address"
            value={device.mac_addresses.length ? device.mac_addresses.join(", ") : null}
          />
          <Detail label="MQTT username" value={`device_${device.id}`} />
          <Detail label="Status topic" value={statusTopic} />
          <Detail label="Metrics topic" value={metricsTopic} />
          <Detail label="Commands topic" value={commandsTopic} />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">On the device</p>
          <pre className="mt-2 overflow-x-auto rounded-md bg-secondary p-3 text-sm">{meteorcliExamples}</pre>
        </div>
        {canManage && (
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={onTestConnection} disabled={pinging}>
              Test Connection
            </Button>
            {pingResult && <p className="text-sm text-green-800">{pingResult}</p>}
          </div>
        )}
        {token ? (
          <MqttConsole
            token={token}
            organizationId={organizationId}
            defaultTopic={statusTopic}
            topicLocked
            canPublish={canManage}
          />
        ) : null}
      </div>

      <div className="grid gap-4 rounded-lg border border-border bg-white/80 p-6 shadow-sm sm:grid-cols-2">
        <Detail
          label="CPU %"
          value={metricText(device.mqtt_metrics, "cpu_percent")}
        />
        <Detail
          label="Memory %"
          value={metricText(device.mqtt_metrics, "memory_percent")}
        />
        <Detail
          label="Disk %"
          value={metricText(device.mqtt_metrics, "disk_percent")}
        />
        <Detail
          label="Temperature °C"
          value={metricText(device.mqtt_metrics, "temperature_c")}
        />
        <Detail
          label="Metrics updated"
          value={device.mqtt_metrics_at ? formatDateTime(device.mqtt_metrics_at) : null}
        />
        <Detail label="Serial number" value={device.serial_number} />
        <Detail label="Operating system" value={[device.os_name, device.os_version].filter(Boolean).join(" ")} />
        <Detail label="Kernel" value={device.kernel_version} />
        <Detail label="Architecture" value={device.architecture} />
        <Detail label="CPU" value={device.cpu_model} />
        <Detail label="CPU cores" value={device.cpu_cores ? String(device.cpu_cores) : null} />
        <Detail label="Memory (MB)" value={device.memory_mb ? String(device.memory_mb) : null} />
        <Detail label="MAC address" value={device.mac_addresses.join(", ")} />
        <Detail
          label="Last seen"
          value={device.last_seen_at ? formatDateTime(device.last_seen_at) : "Never"}
        />
        <Detail label="Created" value={formatDateTime(device.created_at)} />
        <Detail
          label="Registered"
          value={device.registered_at ? formatDateTime(device.registered_at) : null}
        />
        <Detail label="Credential prefix" value={device.credential_prefix} />
      </div>

      {canManage && (
        <div className="space-y-4 rounded-lg border border-border bg-white/80 p-6 shadow-sm">
          <h2 className="text-lg font-semibold">Manage device</h2>

          <div className="grid gap-3 md:grid-cols-[1fr_auto]">
            <div>
              <Label htmlFor="device-name">Name</Label>
              <Input
                id="device-name"
                value={currentName}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="flex items-end">
              <Button onClick={onSave} disabled={currentName === device.name}>
                Save name
              </Button>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label htmlFor="device-type">Device type</Label>
              <select
                id="device-type"
                className="flex h-10 w-full rounded-md border border-input bg-white px-3 text-sm"
                value={device.device_type_id ?? ""}
                onChange={(event) => onAssign("device_type_id", event.target.value)}
              >
                <option value="">Unassigned</option>
                {(typesQuery.data ?? []).map((type) => (
                  <option key={type.id} value={type.id}>
                    {type.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <Label htmlFor="device-group">Device group</Label>
              <select
                id="device-group"
                className="flex h-10 w-full rounded-md border border-input bg-white px-3 text-sm"
                value={device.device_group_id ?? ""}
                onChange={(event) => onAssign("device_group_id", event.target.value)}
              >
                <option value="">Unassigned</option>
                {(groupsQuery.data ?? []).map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => enabledMutation.mutate(!device.is_enabled)}
            >
              {device.is_enabled ? "Disable device" : "Enable device"}
            </Button>
            <Button variant="secondary" onClick={() => rotateMutation.mutate()}>
              Rotate credential
            </Button>
            {device.credential_prefix && (
              <Button variant="ghost" className="text-red-700" onClick={onRevokeCredential}>
                Revoke credential
              </Button>
            )}
            <Button variant="ghost" className="text-red-700" onClick={onDelete}>
              Delete device
            </Button>
          </div>
        </div>
      )}

      {rotatedToken && (
        <OneTimeSecretDialog
          title="Device credential rotated"
          description="Copy this credential now. It replaces the previous one and will not be shown again."
          secret={rotatedToken}
          onClose={() => setRotatedToken(null)}
        />
      )}
    </section>
  );
}
