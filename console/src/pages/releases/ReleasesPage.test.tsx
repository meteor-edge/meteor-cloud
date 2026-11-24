import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createRelease, listReleases } from "@/api/fleet";
import { ReleasesPage } from "@/pages/releases/ReleasesPage";

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ token: "test-token" }),
}));

vi.mock("@/api/organizations", () => ({
  getOrganization: vi.fn(async () => ({
    id: "org-1",
    name: "Acme",
    current_user_role: "developer",
  })),
}));

vi.mock("@/api/fleet", async () => {
  const actual = await vi.importActual<typeof import("@/api/fleet")>("@/api/fleet");
  return {
    ...actual,
    listReleases: vi.fn(async () => ({
      items: [
        {
          id: "rel-1",
          organization_id: "org-1",
          name: "camera-agent",
          version: "1.2.0",
          kind: "systemd",
          artifact_id: "art-1",
          deployment_count: 2,
          artifact: {
            id: "art-1",
            name: "camera-unit",
            version: "1.0",
            type: "systemd",
            file_name: "camera-agent.service",
          },
          description: null,
          spec: { unit_name: "camera-agent.service", start: true },
          created_by_user_id: "user-1",
          created_at: "2026-10-16T12:00:00Z",
          updated_at: "2026-10-16T12:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })),
    deleteRelease: vi.fn(),
    createRelease: vi.fn(async () => ({ id: "rel-2" })),
    getArtifact: vi.fn(async () => ({
      id: "art-1",
      name: "camera-unit",
      version: "1.0",
      type: "systemd",
      file_name: "camera-agent.service",
      release_count: 1,
    })),
    listArtifacts: vi.fn(async () => ({
      items: [{ id: "art-1", name: "camera-unit", version: "1.0", file_name: "camera-agent.service" }],
      total: 1,
      page: 1,
      page_size: 100,
    })),
  };
});

function renderPage(path = "/organizations/org-1/releases") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/organizations/:organizationId/releases" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReleasesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists releases and shows create for developers", async () => {
    renderPage();
    expect(await screen.findByText("camera-agent")).toBeInTheDocument();
    expect(screen.getByText("1.2.0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create release/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^deploy$/i })).toHaveAttribute(
      "href",
      "/organizations/org-1/deployments?deploy=1&release_id=rel-1",
    );
  });

  it("links each release to its artifact", async () => {
    renderPage();
    const link = await screen.findByRole("link", { name: "camera-unit 1.0" });
    expect(link).toHaveAttribute(
      "href",
      "/organizations/org-1/artifacts?type=systemd&search=camera-unit&version=1.0",
    );
    expect(screen.getByText("camera-agent.service")).toBeInTheDocument();
  });

  it("filters to the releases that use one artifact", async () => {
    renderPage("/organizations/org-1/releases?artifact_id=art-1");
    await waitFor(() =>
      expect(screen.getByText(/releases using artifact/i)).toHaveTextContent("camera-unit 1.0"),
    );
    expect(vi.mocked(listReleases).mock.calls[0][2]).toMatchObject({ artifact_id: "art-1" });
  });

  it("opens create release with the artifact preselected", async () => {
    renderPage("/organizations/org-1/releases?create=1&artifact_id=art-1");
    const artifactSelect = await screen.findByLabelText(/^artifact/i);
    await screen.findByRole("option", { name: /camera-unit 1.0/ });
    expect(artifactSelect).toHaveValue("art-1");
    expect(screen.getByLabelText(/^name/i)).toHaveValue("camera-unit");
  });

  it("asks devices to roll back an unhealthy update when chosen", async () => {
    renderPage("/organizations/org-1/releases?create=1&artifact_id=art-1");
    await screen.findByRole("option", { name: /camera-unit 1.0/ });
    fireEvent.change(screen.getByLabelText(/^version/i), { target: { value: "1.3.0" } });
    fireEvent.change(screen.getByLabelText(/unit name/i), { target: { value: "camera-agent.service" } });
    fireEvent.click(screen.getByLabelText(/roll back automatically/i));
    fireEvent.change(screen.getByLabelText(/health url/i), {
      target: { value: "http://127.0.0.1:8080/health" },
    });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create release" }));

    await waitFor(() => expect(createRelease).toHaveBeenCalled());
    const payload = vi.mocked(createRelease).mock.calls[0][2];
    expect(payload.spec).toEqual({
      unit_name: "camera-agent.service",
      start: true,
      health_check: { http_url: "http://127.0.0.1:8080/health" },
    });
  });

  it("links a release to its Deploys and blocks deleting it", async () => {
    renderPage();
    expect(await screen.findByRole("link", { name: "2 Deploys" })).toHaveAttribute(
      "href",
      "/organizations/org-1/deployments?release_id=rel-1",
    );
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });
});
