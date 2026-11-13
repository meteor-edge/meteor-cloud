import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/api/rules";
import { AlertsPage } from "@/pages/rules/AlertsPage";
import { RuleEditorPage } from "@/pages/rules/RuleEditorPage";
import { RulesPage } from "@/pages/rules/RulesPage";

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ token: "test-token" }) }));
vi.mock("@/context/OrganizationContext", () => ({
  useOrganizationContext: () => ({
    organizations: [{ id: "org-1", current_user_role: "developer" }],
  }),
}));
vi.mock("@/api/rules", () => ({
  listAlerts: vi.fn(),
  listSilences: vi.fn(async () => []),
  acknowledgeAlert: vi.fn(async () => ({})),
  createSilence: vi.fn(),
  deleteSilence: vi.fn(),
  listRules: vi.fn(),
  setRuleEnabled: vi.fn(async () => ({})),
  listIntegrations: vi.fn(async () => []),
  createIntegration: vi.fn(),
  updateIntegration: vi.fn(),
  deleteIntegration: vi.fn(),
  getRule: vi.fn(),
  getRuleStatus: vi.fn(),
  listRuleVersions: vi.fn(async () => []),
  validateRule: vi.fn(),
  previewDevices: vi.fn(),
  testRule: vi.fn(),
  createRule: vi.fn(),
  updateRule: vi.fn(),
  deleteRule: vi.fn(),
}));

const alert: api.Alert = {
  id: "alert-1",
  organization_id: "org-1",
  rule_id: "rule-1",
  rule_version: 2,
  rule_name: "High CPU",
  device_id: "dev-12345678",
  episode: 1,
  fingerprint: "",
  severity: "critical",
  status: "firing",
  silenced: false,
  fired_at: "2026-11-13T12:00:00Z",
  acknowledged_at: null,
  acknowledged_by_user_id: null,
  resolved_at: null,
  resolve_reason: null,
  trigger_event_id: "ev-1",
  trigger_values: { cpu_percent: 97.5 },
};

function renderAt(path: string, pattern: string, element: ReactNode) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={pattern} element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("AlertsPage", () => {
  it("lists active alerts and acknowledges one", async () => {
    vi.mocked(api.listAlerts).mockResolvedValue([alert]);
    renderAt(
      "/organizations/org-1/alerts",
      "/organizations/:organizationId/alerts",
      <AlertsPage />,
    );
    await waitFor(() => expect(screen.getByText("High CPU")).toBeInTheDocument());
    expect(screen.getByText("cpu_percent=97.5")).toBeInTheDocument();
    expect(api.listAlerts).toHaveBeenCalledWith("test-token", "org-1", { limit: 50, active: true });
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() =>
      expect(api.acknowledgeAlert).toHaveBeenCalledWith("test-token", "org-1", "alert-1"),
    );
  });
});

describe("RulesPage", () => {
  it("lists rules and toggles one", async () => {
    vi.mocked(api.listRules).mockResolvedValue([
      {
        id: "rule-1",
        organization_id: "org-1",
        name: "High CPU",
        enabled: true,
        current_version: 3,
        scope_type: "device_type",
        severity: "warning",
        definition: {},
        created_by_user_id: null,
        created_at: "2026-11-13T12:00:00Z",
        updated_at: "2026-11-13T12:00:00Z",
      },
    ]);
    renderAt("/organizations/org-1/rules", "/organizations/:organizationId/rules", <RulesPage />);
    await waitFor(() => expect(screen.getByText("High CPU")).toBeInTheDocument());
    expect(screen.getByText("device type")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New rule" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Enable High CPU"));
    await waitFor(() =>
      expect(api.setRuleEnabled).toHaveBeenCalledWith("test-token", "org-1", "rule-1", false),
    );
  });
});

describe("RuleEditorPage", () => {
  const editor = () =>
    renderAt(
      "/organizations/org-1/rules/new",
      "/organizations/:organizationId/rules/:ruleId",
      <RuleEditorPage />,
    );

  it("shows validation problems from the server", async () => {
    vi.mocked(api.validateRule).mockResolvedValue({
      valid: false,
      problems: [{ path: "severity", message: "must be info, warning or critical" }],
      condition_hash: null,
    });
    editor();
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() =>
      expect(screen.getByText("must be info, warning or critical")).toBeInTheDocument(),
    );
    const [, , definition] = vi.mocked(api.validateRule).mock.calls[0];
    expect(definition).toMatchObject({ schema_version: 1, name: "High CPU" });
  });

  it("refuses invalid JSON before calling the server", async () => {
    editor();
    fireEvent.change(screen.getByLabelText("Rule definition (JSON)"), {
      target: { value: "{nope" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    expect(await screen.findByText(/not valid JSON/)).toBeInTheDocument();
    expect(api.createRule).not.toHaveBeenCalled();
  });

  it("runs a test and shows the transitions", async () => {
    vi.mocked(api.testRule).mockResolvedValue({
      steps: [
        { at: "2026-11-13T12:00:00Z", phase: "pending", values: { cpu_percent: 95 } },
        {
          at: "2026-11-13T12:05:00Z",
          phase: "firing",
          timer: true,
          transitions: [{ transition: "firing", episode: 1 }],
        },
      ],
    });
    editor();
    fireEvent.click(screen.getByRole("button", { name: "Run test" }));
    await waitFor(() => expect(screen.getByText("pending")).toBeInTheDocument());
    expect(screen.getAllByText("firing").length).toBeGreaterThan(0);
    expect(vi.mocked(api.testRule).mock.calls[0][3]).toHaveLength(5);
  });

  it("creates a rule and opens it", async () => {
    vi.mocked(api.createRule).mockResolvedValue({ id: "rule-9" } as api.Rule);
    vi.mocked(api.getRule).mockResolvedValue({
      id: "rule-9",
      name: "High CPU",
      current_version: 1,
      enabled: true,
      definition: { schema_version: 1 },
    } as unknown as api.Rule);
    vi.mocked(api.getRuleStatus).mockResolvedValue({
      rule_id: "rule-9",
      current_version: 1,
      enabled: true,
      devices: [],
    });
    editor();
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(api.getRule).toHaveBeenCalledWith("test-token", "org-1", "rule-9"));
    expect(await screen.findByRole("button", { name: "Save new version" })).toBeInTheDocument();
    expect(await screen.findByText(/No device has reported/)).toBeInTheDocument();
  });
});
