import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { listAuditEvents } from "@/api/audit";
import { AuditLogPage } from "@/pages/organizations/AuditLogPage";

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ token: "test-token" }),
}));

vi.mock("@/api/audit", () => ({
  listAuditEvents: vi.fn(async () => [
    {
      id: "evt-1",
      organization_id: "org-1",
      actor_user_id: "user-1",
      actor_type: "user",
      actor_id: "user-1",
      actor_email: "owner@example.com",
      action: "device.delete",
      resource_type: "device",
      resource_id: "dev-1",
      request_id: "req-1",
      ip: "203.0.113.7",
      user_agent: "browser",
      outcome: "success",
      metadata: { name: "edge-01" },
      created_at: "2026-11-12T05:00:00Z",
    },
    {
      id: "evt-2",
      organization_id: "org-1",
      actor_user_id: null,
      actor_type: "device",
      actor_id: "0123456789abcdef",
      actor_email: null,
      action: "access.denied",
      resource_type: "route",
      resource_id: null,
      request_id: null,
      ip: null,
      user_agent: null,
      outcome: "denied",
      metadata: {},
      created_at: "2026-11-12T04:00:00Z",
    },
  ]),
}));

describe("AuditLogPage", () => {
  it("lists audit events with actor, action and outcome", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/organizations/org-1/audit-log"]}>
          <Routes>
            <Route path="/organizations/:organizationId/audit-log" element={<AuditLogPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText("device.delete")).toBeInTheDocument());
    expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
    expect(screen.getByText("device 01234567")).toBeInTheDocument();
    expect(screen.getByText("denied", { selector: "td" })).toBeInTheDocument();
    expect(listAuditEvents).toHaveBeenCalledWith("test-token", "org-1", { limit: 50 });
  });
});
