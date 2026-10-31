import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeviceResourceMeters } from "@/components/fleet/DeviceResourceMeters";

describe("DeviceResourceMeters", () => {
  it("renders nothing when metrics are missing", () => {
    const { container } = render(<DeviceResourceMeters metrics={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows healthy meters for normal load", () => {
    render(
      <DeviceResourceMeters
        metrics={{
          cpu_percent: 18.4,
          memory_percent: 42.1,
          disk_percent: 35.7,
          temperature_c: 49.2,
        }}
        updatedAt="10/31/2026, 10:55:33 AM"
      />,
    );

    expect(screen.getByText("Live resources")).toBeInTheDocument();
    expect(screen.getByText("Updated 10/31/2026, 10:55:33 AM")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "CPU" })).toHaveAttribute(
      "aria-valuetext",
      "18.4% (Healthy)",
    );
    expect(screen.getAllByText("Healthy")).toHaveLength(4);
  });

  it("marks elevated and critical levels with brand tones", () => {
    render(
      <DeviceResourceMeters
        metrics={{
          cpu_percent: 78,
          memory_percent: 93,
          disk_percent: 12,
          temperature_c: 81,
        }}
      />,
    );

    expect(screen.getByRole("progressbar", { name: "CPU" })).toHaveAttribute(
      "aria-valuetext",
      "78.0% (Elevated)",
    );
    expect(screen.getByRole("progressbar", { name: "Memory" })).toHaveAttribute(
      "aria-valuetext",
      "93.0% (Critical)",
    );
    expect(screen.getByRole("progressbar", { name: "Temperature" })).toHaveAttribute(
      "aria-valuetext",
      "81.0 °C (Critical)",
    );
    expect(screen.getByText("Elevated")).toBeInTheDocument();
    expect(screen.getAllByText("Critical")).toHaveLength(2);
  });

  it("omits meters that have no value", () => {
    render(<DeviceResourceMeters metrics={{ cpu_percent: 12 }} />);
    expect(screen.getByRole("progressbar", { name: "CPU" })).toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Memory" })).not.toBeInTheDocument();
  });
});
