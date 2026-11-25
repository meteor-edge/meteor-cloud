import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getOrganization } from "@/api/organizations";
import {
  createRegistryToken,
  getRegistry,
  listRegistryTokens,
  revokeRegistryToken,
  type RegistryToken,
} from "@/api/registry";
import { RegistrySettingsPage } from "@/pages/organizations/RegistrySettingsPage";

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ token: "test-token" }) }));
vi.mock("@/api/organizations", () => ({ getOrganization: vi.fn() }));
vi.mock("@/api/registry", () => ({
  getRegistry: vi.fn(),
  listRegistryTokens: vi.fn(),
  createRegistryToken: vi.fn(),
  revokeRegistryToken: vi.fn(),
}));

const entry: RegistryToken = {
  id: "tok-1",
  name: "ci",
  kind: "robot",
  access: "push",
  token_prefix: "rgt_abcdefgh",
  repository_prefix: "ci",
  user_id: null,
  created_by_user_id: "user-1",
  created_at: "2026-11-25T10:00:00Z",
  expires_at: null,
  revoked_at: null,
  last_used_at: null,
};

function renderPage(role: string, enabled = true) {
  vi.mocked(getOrganization).mockResolvedValue({ name: "Acme", current_user_role: role } as never);
  vi.mocked(getRegistry).mockResolvedValue(
    enabled
      ? { enabled, host: "registry.example.com", namespace: "acme" }
      : { enabled, host: null, namespace: null },
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/organizations/org-1/registry"]}>
        <Routes>
          <Route path="/organizations/:organizationId/registry" element={<RegistrySettingsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RegistrySettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listRegistryTokens).mockResolvedValue([entry]);
  });

  it("creates a robot token and shows the secret once with a login command", async () => {
    vi.mocked(createRegistryToken).mockResolvedValue({ ...entry, token: "rgt_secret" });
    renderPage("owner");
    fireEvent.change(await screen.findByLabelText("Used by"), { target: { value: "robot" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "builds" } });
    fireEvent.change(screen.getByLabelText(/Only repositories/), { target: { value: "ci" } });
    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByText("rgt_secret")).toBeInTheDocument();
    expect(screen.getByText("docker login registry.example.com -u robot")).toBeInTheDocument();
    expect(createRegistryToken).toHaveBeenCalledWith("test-token", "org-1", {
      name: "builds",
      kind: "robot",
      access: "push",
      repository_prefix: "ci",
      expires_at: null,
    });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("rgt_secret")).not.toBeInTheDocument();
  });

  it("offers viewers pull-only personal tokens", async () => {
    vi.mocked(createRegistryToken).mockResolvedValue({ ...entry, token: "rgt_secret" });
    renderPage("viewer");
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "laptop" } });
    expect(screen.queryByLabelText("Used by")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Access")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    await waitFor(() =>
      expect(createRegistryToken).toHaveBeenCalledWith(
        "test-token",
        "org-1",
        expect.objectContaining({ kind: "personal", access: "pull" }),
      ),
    );
  });

  it("revokes after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(revokeRegistryToken).mockResolvedValue(entry);
    renderPage("owner");
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await waitFor(() =>
      expect(revokeRegistryToken).toHaveBeenCalledWith("test-token", "org-1", "tok-1"),
    );
  });

  it("says when the registry is off", async () => {
    renderPage("owner", false);
    expect(await screen.findByText(/not enabled on this platform/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create token" })).not.toBeInTheDocument();
    expect(listRegistryTokens).not.toHaveBeenCalled();
  });
});
