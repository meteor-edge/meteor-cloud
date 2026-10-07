import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UploadProgress } from "@/components/artifacts/UploadProgress";

describe("UploadProgress", () => {
  it("keeps an unknown total at zero while measuring speed", () => {
    render(
      <UploadProgress fileName="disk.img" state={{ loaded: 0, total: 0, bytesPerSecond: null }} />,
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("Measuring speed…")).toBeInTheDocument();
    expect(screen.queryByText("Saving to storage…")).not.toBeInTheDocument();
    expect(screen.queryByText(/left/)).not.toBeInTheDocument();
  });

  it("shows bytes, speed, and remaining time with accessible progress", () => {
    render(
      <UploadProgress
        fileName="disk.img"
        state={{ loaded: 1024, total: 4096, bytesPerSecond: 1024 }}
      />,
    );
    expect(screen.getByRole("progressbar", { name: "Upload progress" })).toHaveAttribute(
      "aria-valuenow",
      "25",
    );
    expect(screen.getByText("1.0 KB of 4.0 KB")).toBeInTheDocument();
    expect(screen.getByText("1.0 KB/s · about 3 s left")).toBeInTheDocument();
  });

  it.each([4096, 4100])(
    "switches to storage processing at %i bytes and caps progress",
    (loaded) => {
      render(
        <UploadProgress
          fileName="disk.img"
          state={{ loaded, total: 4096, bytesPerSecond: 1024 }}
        />,
      );
      const progress = screen.getByRole("progressbar");
      expect(progress).toHaveAttribute("aria-valuenow", "100");
      expect(progress).toHaveAttribute("aria-valuetext", "Saving to storage");
      expect(screen.getByText("Saving to storage…")).toBeInTheDocument();
      expect(screen.queryByText(/about .* left/)).not.toBeInTheDocument();
    },
  );

  it("does not estimate completion when transfer speed is zero", () => {
    render(
      <UploadProgress fileName="disk.img" state={{ loaded: 1, total: 3, bytesPerSecond: 0 }} />,
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "33");
    expect(screen.getByText("Measuring speed…")).toBeInTheDocument();
    expect(screen.queryByText(/left/)).not.toBeInTheDocument();
  });
});
