import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getOrganization } from "@/api/organizations";
import { deleteImage, getRegistry, listImages, listImageTags } from "@/api/registry";
import { ImagesPage } from "@/pages/images/ImagesPage";

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ token: "test-token" }) }));
vi.mock("@/api/organizations", () => ({ getOrganization: vi.fn() }));
vi.mock("@/api/registry", () => ({
  getRegistry: vi.fn(),
  listImages: vi.fn(),
  listImageTags: vi.fn(),
  deleteImage: vi.fn(),
}));

function renderPage(role = "owner", total = 1) {
  vi.mocked(getOrganization).mockResolvedValue({ name: "Acme", current_user_role: role } as never);
  vi.mocked(getRegistry).mockResolvedValue({
    enabled: true,
    host: "registry.example.com",
    namespace: "acme",
  });
  vi.mocked(listImages).mockResolvedValue({
    items: total ? [{ repository: "web", tag_count: 1, pushed_at: "2026-11-25T10:00:00Z" }] : [],
    total,
    page: 1,
    page_size: 20,
  });
  vi.mocked(listImageTags).mockResolvedValue({
    items: [
      {
        repository: "web",
        tag: "1.0",
        digest: `sha256:${"a".repeat(64)}`,
        media_type: "application/vnd.oci.image.index.v1+json",
        size_bytes: 2048,
        platforms: [
          { os: "linux", architecture: "amd64", variant: null, size_bytes: 1024 },
          { os: "linux", architecture: "arm64", variant: "v8", size_bytes: 1024 },
        ],
        pushed_at: "2026-11-25T10:00:00Z",
        pushed_by: "ci",
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/organizations/org-1/images"]}>
        <Routes>
          <Route path="/organizations/:organizationId/images" element={<ImagesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ImagesPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows tags, platforms and copies the pull command", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "web" }));
    expect(await screen.findByText("linux/amd64, linux/arm64/v8")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy pull command for web:1.0" }));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("docker pull registry.example.com/acme/web:1.0"),
    );
  });

  it("shows the pull command when it cannot be copied", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "web" }));
    fireEvent.click(await screen.findByRole("button", { name: "Copy pull command for web:1.0" }));
    expect(
      await screen.findByText("docker pull registry.example.com/acme/web:1.0"),
    ).toBeInTheDocument();
  });

  it("leads back when the last page becomes empty", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage("owner", 21);
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(await screen.findByText(/Page 2 of 2/)).toBeInTheDocument();
    vi.mocked(listImages).mockImplementation(async (_token, _org, { page }) => ({
      items: page === 1 ? [{ repository: "first", tag_count: 1, pushed_at: "2026-11-25T10:00:00Z" }] : [],
      total: 20,
      page: page ?? 1,
      page_size: 20,
    }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete web" }));
    await waitFor(() => expect(screen.queryByText("web")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("first")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Previous" })).not.toBeInTheDocument();
  });

  it("deletes a tag after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "web" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete web:1.0" }));
    await waitFor(() =>
      expect(deleteImage).toHaveBeenCalledWith("test-token", "org-1", "web", "1.0"),
    );
  });

  it("hides delete from developers", async () => {
    renderPage("developer");
    await screen.findByRole("button", { name: "web" });
    expect(screen.queryByRole("button", { name: "Delete web" })).not.toBeInTheDocument();
  });

  it("explains how to push when there are no images", async () => {
    renderPage("viewer", 0);
    expect(await screen.findByText(/docker push registry.example.com\/acme\/my-app/)).toBeInTheDocument();
  });
});
