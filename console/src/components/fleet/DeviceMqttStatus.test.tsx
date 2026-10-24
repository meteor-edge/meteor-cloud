import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeviceMqttStatus } from "@/components/fleet/DeviceMqttStatus";
import { mqttStateOf } from "@/lib/mqttStatus";

const NOW = Date.parse("2026-10-24T12:00:00Z");
const base = {
  mqtt_configured: true,
  mqtt_status: null,
  mqtt_status_at: null,
  mqtt_metrics_at: null,
} as const;

describe("mqttStateOf", () => {
  it("is connected while metrics keep arriving, even long after the online status", () => {
    const device = {
      ...base,
      mqtt_status: "online" as const,
      mqtt_status_at: "2026-10-24T08:00:00Z",
      mqtt_metrics_at: "2026-10-24T11:59:10Z",
    };
    expect(mqttStateOf(device, NOW)).toBe("connected");
  });

  it("is stale when an online device stops publishing", () => {
    const device = {
      ...base,
      mqtt_status: "online" as const,
      mqtt_status_at: "2026-10-24T11:00:00Z",
      mqtt_metrics_at: "2026-10-24T11:50:00Z",
    };
    expect(mqttStateOf(device, NOW)).toBe("stale");
  });

  it("is disconnected after the broker publishes the last will", () => {
    expect(mqttStateOf({ ...base, mqtt_status: "offline" }, NOW)).toBe("disconnected");
  });

  it("waits for a configured device that has not connected", () => {
    expect(mqttStateOf(base, NOW)).toBe("waiting");
    expect(mqttStateOf({ ...base, mqtt_configured: false }, NOW)).toBe("not_configured");
  });
});

describe("DeviceMqttStatus", () => {
  it("shows a green connected state and healthy messaging", () => {
    const { container } = render(
      <DeviceMqttStatus
        now={NOW}
        device={{ ...base, mqtt_status: "online", mqtt_status_at: "2026-10-24T11:59:30Z" }}
      />,
    );
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(container.querySelector(".bg-green-500")).not.toBeNull();
  });
});
