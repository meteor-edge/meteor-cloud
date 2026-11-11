import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Artifact } from "@/api/fleet";
import { ArtifactTable } from "@/components/artifacts/ArtifactTable";

function artifact(overrides: Partial<Artifact>): Artifact {
  return {
    id: "art-1",
    organization_id: "org-1",
    device_type_id: null,
    name: "camera-unit",
    version: "1.0",
    type: "systemd",
    description: null,
    file_name: "camera-agent.service",
    content_type: "text/plain",
    size_bytes: 100,
    checksum_sha256: "a".repeat(64),
    metadata: {},
    created_by_user_id: null,
    created_at: "2026-10-16T12:00:00Z",
    updated_at: "2026-10-16T12:00:00Z",
    release_count: 0,
    ...overrides,
  };
}

function renderTable(artifacts: Artifact[]) {
  return render(
    <MemoryRouter>
      <ArtifactTable
        artifacts={artifacts}
        canManage
        onDownload={vi.fn()}
        onDelete={vi.fn()}
        organizationId="org-1"
      />
    </MemoryRouter>,
  );
}

describe("ArtifactTable release links", () => {
  it("links to the releases using an artifact and blocks deleting it", () => {
    renderTable([artifact({ release_count: 2 })]);
    expect(screen.getByRole("link", { name: "2 releases" })).toHaveAttribute(
      "href",
      "/organizations/org-1/releases?artifact_id=art-1",
    );
    expect(screen.getByRole("button", { name: /^Delete/ })).toBeDisabled();
  });

  it("offers Create release for installable types only", () => {
    renderTable([artifact({}), artifact({ id: "art-2", type: "os_image", name: "rpi-os" })]);
    const links = screen.getAllByRole("link", { name: "Create release" });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute(
      "href",
      "/organizations/org-1/releases?create=1&artifact_id=art-1",
    );
    expect(screen.getAllByRole("button", { name: /^Delete/ })[0]).toBeEnabled();
  });
});

describe("ArtifactTable Meteor Imager column", () => {
  it("shows a switch for OS images only and reports toggles", () => {
    const onTogglePublished = vi.fn();
    const image = artifact({ id: "art-2", type: "os_image", name: "rpi-os", published: true });
    render(
      <MemoryRouter>
        <ArtifactTable
          artifacts={[artifact({}), image]}
          canManage
          onDownload={vi.fn()}
          onDelete={vi.fn()}
          onTogglePublished={onTogglePublished}
          canPublish
        />
      </MemoryRouter>,
    );
    const toggle = screen.getByRole("checkbox", { name: "Show rpi-os 1.0 in Meteor Imager" });
    expect(toggle).toBeChecked();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.click(toggle);
    expect(onTogglePublished).toHaveBeenCalledWith(image);
  });

  it("is read-only for members who manage artifacts but cannot publish", () => {
    render(
      <MemoryRouter>
        <ArtifactTable
          artifacts={[artifact({ type: "os_image", name: "rpi-os" })]}
          canManage
          onDownload={vi.fn()}
          onDelete={vi.fn()}
          onTogglePublished={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("checkbox", { name: "Show rpi-os 1.0 in Meteor Imager" })).toBeDisabled();
  });

  it("hides the column when no handler is given", () => {
    renderTable([artifact({ type: "os_image" })]);
    expect(screen.queryByText("Meteor Imager")).not.toBeInTheDocument();
  });
});
