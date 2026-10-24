import type { Device } from "@/api/fleet";

/** The agent sends metrics every 60 s; three missed intervals means messaging has stalled. */
export const MQTT_STALE_AFTER_MS = 3 * 60_000;

export type MqttState = "connected" | "stale" | "disconnected" | "waiting" | "not_configured";

export type MqttFields = Pick<
  Device,
  "mqtt_configured" | "mqtt_status" | "mqtt_status_at" | "mqtt_metrics_at"
>;

export function latest(...values: (string | null)[]): string | null {
  const times = values.filter((v): v is string => Boolean(v));
  if (!times.length) return null;
  return times.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b));
}

export function mqttStateOf(device: MqttFields, now: number = Date.now()): MqttState {
  if (device.mqtt_status === "offline") return "disconnected";
  if (device.mqtt_status === "online") {
    const last = latest(device.mqtt_status_at, device.mqtt_metrics_at);
    return last && now - Date.parse(last) <= MQTT_STALE_AFTER_MS ? "connected" : "stale";
  }
  return device.mqtt_configured ? "waiting" : "not_configured";
}
