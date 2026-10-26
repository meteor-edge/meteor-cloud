import { latest, mqttStateOf, type MqttFields, type MqttState } from "@/lib/mqttStatus";
import { formatDateTime } from "@/lib/utils";

const STATES: Record<MqttState, { label: string; dot: string; hint: string }> = {
  connected: { label: "Connected", dot: "bg-green-500", hint: "" },
  stale: {
    label: "No recent messages",
    dot: "bg-amber-500",
    hint: "The device reported online but has not published for a few minutes. The broker marks it offline if the connection is lost.",
  },
  disconnected: {
    label: "Disconnected",
    dot: "bg-destructive",
    hint: "The device left the broker. The agent reconnects on its own.",
  },
  waiting: {
    label: "Waiting for the device",
    dot: "bg-muted-foreground",
    hint: "The agent connects to MQTT on its next heartbeat and fetches new credentials if needed.",
  },
  not_configured: {
    label: "Not configured",
    dot: "bg-muted-foreground",
    hint: "This device has no MQTT credentials yet.",
  },
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  );
}

/** Broker connection and message health, styled like the WireGuard status. */
export function DeviceMqttStatus({ device, now }: { device: MqttFields; now?: number }) {
  const state = mqttStateOf(device, now);
  const shown = STATES[state];
  const last = latest(device.mqtt_status_at, device.mqtt_metrics_at);
  return (
    <div className="space-y-2">
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Status">
          <span className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${shown.dot}`} />
            {shown.label}
          </span>
        </Field>
        <Field label="Messaging">
          {state === "connected" ? "Healthy" : state === "stale" ? "Stalled" : "—"}
        </Field>
        <Field label="Last message">{last ? formatDateTime(last) : "Never"}</Field>
      </div>
      {shown.hint && <p className="text-sm text-muted-foreground">{shown.hint}</p>}
    </div>
  );
}
