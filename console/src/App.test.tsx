import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as authApi from "@/api/auth";
import { ApiError } from "@/api/http";
import * as orgApi from "@/api/organizations";
import { App } from "@/App";
import { AuthProvider } from "@/auth/AuthContext";
import { OrganizationProvider } from "@/context/OrganizationContext";

vi.mock("@/api/auth");
vi.mock("@/api/organizations");

function renderApp(initialEntries: string[] = ["/"]) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>
          <OrganizationProvider>
            <App />
          </OrganizationProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const ownerUser = {
  id: "user-1",
  email: "owner@example.com",
  full_name: "Example Owner",
  is_active: true,
  created_at: new Date().toISOString(),
};

const acmeOrg = {
  id: "org-1",
  name: "Acme Energy",
  slug: "acme-energy",
  description: "Demo org",
  created_by_user_id: "user-1",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  current_user_role: "owner" as const,
  member_count: 4,
};

describe("App shell and auth", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetAllMocks();
  });

  it("renders the landing page", () => {
    renderApp(["/"]);
    expect(
      screen.getByRole("heading", {
        name: /identity and organizations for the control plane/i,
      }),
    ).toBeInTheDocument();
  });

  it("validates registration password length", async () => {
    const user = userEvent.setup();
    renderApp(["/register"]);

    await user.type(screen.getByLabelText(/full name/i), "Example Owner");
    await user.type(screen.getByLabelText(/^email$/i), "owner@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "short");
    await user.click(screen.getByRole("button", { name: /register/i }));

    expect(await screen.findByText(/at least 10 characters/i)).toBeInTheDocument();
  });

  it("logs in successfully and stores the token", async () => {
    const user = userEvent.setup();
    vi.mocked(authApi.loginUser).mockResolvedValue({
      access_token: "token-123",
      token_type: "bearer",
      expires_in: 1800,
    });
    vi.mocked(authApi.fetchCurrentUser).mockResolvedValue(ownerUser);
    vi.mocked(orgApi.listOrganizations).mockResolvedValue([acmeOrg]);

    renderApp(["/login"]);
    await user.type(screen.getByLabelText(/^email$/i), "owner@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "strong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(authApi.loginUser).toHaveBeenCalledWith({
        email: "owner@example.com",
        password: "strong-password",
      });
      expect(localStorage.getItem("edge_platform_access_token")).toBe("token-123");
    });
  });

  it("shows login error on failure", async () => {
    const user = userEvent.setup();
    vi.mocked(authApi.loginUser).mockRejectedValue(
      new ApiError(401, "invalid_credentials", "Invalid email or password."),
    );

    renderApp(["/login"]);
    await user.type(screen.getByLabelText(/^email$/i), "owner@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "wrong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText(/invalid email or password/i)).toBeInTheDocument();
  });

  it("redirects protected routes to login", async () => {
    renderApp(["/organizations"]);
    expect(await screen.findByRole("heading", { name: /^sign in$/i })).toBeInTheDocument();
  });
});

describe("Organization UI", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("edge_platform_access_token", "token-123");
    vi.resetAllMocks();
    vi.mocked(authApi.fetchCurrentUser).mockResolvedValue(ownerUser);
    vi.mocked(orgApi.listOrganizations).mockResolvedValue([acmeOrg]);
  });

  it("renders organization list", async () => {
    renderApp(["/organizations"]);
    expect(await screen.findByText(/role: owner/i)).toBeInTheDocument();
    expect(screen.getAllByText("Acme Energy").length).toBeGreaterThan(0);
  });

  it("creates an organization", async () => {
    const user = userEvent.setup();
    vi.mocked(orgApi.listOrganizations).mockResolvedValue([]);
    vi.mocked(orgApi.createOrganization).mockResolvedValue({
      ...acmeOrg,
      id: "org-2",
      name: "New Org",
      slug: "new-org",
      description: null,
      member_count: 1,
    });
    vi.mocked(orgApi.getOrganization).mockResolvedValue({
      ...acmeOrg,
      id: "org-2",
      name: "New Org",
      slug: "new-org",
      description: null,
      member_count: 1,
    });

    renderApp(["/organizations/new"]);
    expect(
      await screen.findByRole("heading", { name: /create organization/i }),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^name$/i), "New Org");
    await user.clear(screen.getByLabelText(/^slug$/i));
    await user.type(screen.getByLabelText(/^slug$/i), "new-org");
    await user.click(screen.getByRole("button", { name: /create organization/i }));

    await waitFor(() => {
      expect(orgApi.createOrganization).toHaveBeenCalled();
    });
  });

  it("shows duplicate slug error", async () => {
    const user = userEvent.setup();
    vi.mocked(orgApi.listOrganizations).mockResolvedValue([]);
    vi.mocked(orgApi.createOrganization).mockRejectedValue(
      new ApiError(
        409,
        "organization_slug_exists",
        "An organization with this slug already exists.",
      ),
    );

    renderApp(["/organizations/new"]);
    expect(
      await screen.findByRole("heading", { name: /create organization/i }),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^name$/i), "Acme");
    await user.click(screen.getByRole("button", { name: /create organization/i }));

    expect(
      await screen.findByText(/organization with this slug already exists/i),
    ).toBeInTheDocument();
  });

  it("hides member management for viewers", async () => {
    vi.mocked(orgApi.getOrganization).mockResolvedValue({
      ...acmeOrg,
      current_user_role: "viewer",
      member_count: 2,
    });
    vi.mocked(orgApi.listMembers).mockResolvedValue([
      {
        id: "m-1",
        organization_id: "org-1",
        user_id: "user-1",
        email: "viewer@example.com",
        full_name: "Viewer",
        role: "viewer",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]);

    renderApp(["/organizations/org-1/members"]);
    expect(await screen.findByText("viewer@example.com")).toBeInTheDocument();
    expect(screen.queryByLabelText(/add existing user by email/i)).not.toBeInTheDocument();
  });
});
