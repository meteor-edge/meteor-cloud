import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/api/rules";
import { AlertsPage } from "@/pages/rules/AlertsPage";
import { ChannelsPage } from "@/pages/rules/ChannelsPage";
import { DeviceHealthPage } from "@/pages/rules/DeviceHealthPage";
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
  listMetrics: vi.fn(),
  queryLiveDevices: vi.fn(),
  getAlertSummary: vi.fn(),
  getChannelCapabilities: vi.fn(),
  testIntegration: vi.fn(),
}));
vi.mock("@/api/fleet/devices", () => ({
  listDevices: vi.fn(async () => ({ items: [{ id: "dev-2", name: "pump-2" }], total: 1 })),
  getDevice: vi.fn(async () => ({ id: "dev-2", name: "pump-2" })),
}));
vi.mock("@/api/fleet/deviceGroups", () => ({
  listDeviceGroups: vi.fn(async () => [{ id: "grp-1", name: "Stores" }]),
}));
vi.mock("@/api/fleet/deviceTypes", () => ({ listDeviceTypes: vi.fn(async () => []) }));

const metric = (field: string, label: string, devices = 2): api.Metric => ({
  field,
  label,
  unit: "%",
  group: "System",
  description: "",
  suggested_operator: "gt",
  suggested_value: 80,
  devices,
  min: 10,
  max: 95,
});
const mailChannel: api.Integration = {
  id: "int-1",
  organization_id: "org-1",
  kind: "email",
  name: "Ops mail",
  url: "",
  recipients: ["ops@example.com"],
  chat_id: null,
  enabled: true,
  has_secret: false,
  created_at: "2026-11-13T12:00:00Z",
  updated_at: "2026-11-13T12:00:00Z",
};
const liveDevice: api.LiveDevice = {
  id: "dev-1",
  name: "pump-1",
  device_group_id: null,
  device_type_id: null,
  online: true,
  metrics_at: "2026-11-13T12:00:00Z",
  fresh: true,
  metrics: { cpu_percent: 93.26 },
  alerts: [
    { id: "a", rule_id: "r", rule_name: "High CPU", severity: "critical", status: "firing" },
  ],
};

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

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listMetrics).mockResolvedValue([
    metric("cpu_percent", "CPU usage"),
    metric("memory_percent", "Memory usage"),
    metric("app.web.cpu", "web: CPU", 0),
  ]);
  vi.mocked(api.queryLiveDevices).mockResolvedValue({
    total: 3,
    matching: 1,
    devices: [liveDevice],
  });
  vi.mocked(api.getAlertSummary).mockResolvedValue({
    firing: 4,
    acknowledged: 1,
    devices: 3,
    by_severity: { critical: 2 },
    by_rule: { "rule-1": 4 },
  });
  vi.mocked(api.getChannelCapabilities).mockResolvedValue({ engine: true, email: true });
  vi.mocked(api.listIntegrations).mockResolvedValue([mailChannel]);
  vi.mocked(api.listRules).mockResolvedValue([]);
});

describe("AlertsPage", () => {
  it("lists active alerts and acknowledges one", async () => {
    vi.mocked(api.listAlerts).mockResolvedValue([alert]);
    renderAt(
      "/organizations/org-1/alerts",
      "/organizations/:organizationId/alerts",
      <AlertsPage />,
    );
    await waitFor(() => expect(screen.getByText("High CPU")).toBeInTheDocument());
    expect(await screen.findByText("CPU usage 97.5%")).toBeInTheDocument();
    expect(api.listAlerts).toHaveBeenCalledWith("test-token", "org-1", { limit: 50, active: true });
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() =>
      expect(api.acknowledgeAlert).toHaveBeenCalledWith("test-token", "org-1", "alert-1"),
    );
  });

  it("shows counts and filters by severity, metric and text", async () => {
    vi.mocked(api.listAlerts).mockResolvedValue([{ ...alert, device_name: "pump-1" }]);
    renderAt(
      "/organizations/org-1/alerts?rule_id=rule-1",
      "/organizations/:organizationId/alerts",
      <AlertsPage />,
    );
    expect(await screen.findByText("pump-1")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Critical/ }));
    fireEvent.change(screen.getByLabelText("Metric"), { target: { value: "memory_percent" } });
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "pump" } });
    await waitFor(() =>
      expect(api.listAlerts).toHaveBeenLastCalledWith("test-token", "org-1", {
        limit: 50,
        active: true,
        severity: "critical",
        rule_id: "rule-1",
        metric: "memory_percent",
        search: "pump",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Silence 2 h" }));
    await waitFor(() =>
      expect(vi.mocked(api.createSilence).mock.calls[0][2]).toMatchObject({
        rule_id: "rule-1",
        device_id: "dev-12345678",
      }),
    );
  });
});

describe("DeviceHealthPage", () => {
  it("filters devices by readings and offers the filter as a rule", async () => {
    renderAt(
      "/organizations/org-1/device-health",
      "/organizations/:organizationId/device-health",
      <DeviceHealthPage />,
    );
    expect(await screen.findByText("pump-1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "CPU or memory > 80%" }));
    await waitFor(() =>
      expect(vi.mocked(api.queryLiveDevices).mock.lastCall?.[2]).toMatchObject({
        match: "any",
        conditions: [
          { field: "cpu_percent", operator: "gt", value: 80 },
          { field: "memory_percent", operator: "gt", value: 80 },
        ],
      }),
    );
    expect(screen.getByText("93.3%")).toHaveClass("text-destructive");
    expect(await screen.findByRole("status")).toHaveTextContent("1 of 3 devices match");
    expect(screen.getByRole("link", { name: "Create rule from this filter" })).toHaveAttribute(
      "href",
      "/organizations/org-1/rules/new?c=cpu_percent%3Agt%3A80%3A&c=memory_percent%3Agt%3A80%3A&match=any",
    );
  });
});

describe("ChannelsPage", () => {
  const open = () =>
    renderAt(
      "/organizations/org-1/channels",
      "/organizations/:organizationId/channels",
      <ChannelsPage />,
    );

  it("adds a Telegram bot and email recipients", async () => {
    vi.mocked(api.createIntegration).mockResolvedValue(mailChannel);
    open();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ops" } });
    fireEvent.change(screen.getByLabelText("Recipients"), {
      target: { value: "a@example.com, b@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add channel" }));
    await waitFor(() =>
      expect(api.createIntegration).toHaveBeenCalledWith("test-token", "org-1", {
        kind: "email",
        name: "Ops",
        recipients: ["a@example.com", "b@example.com"],
      }),
    );
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue(""));
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "telegram" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bot" } });
    fireEvent.change(screen.getByLabelText("Bot token"), { target: { value: "1:abc" } });
    fireEvent.change(screen.getByLabelText("Chat ID"), { target: { value: "-100" } });
    fireEvent.click(screen.getByRole("button", { name: "Add channel" }));
    await waitFor(() =>
      expect(api.createIntegration).toHaveBeenLastCalledWith("test-token", "org-1", {
        kind: "telegram",
        name: "Bot",
        chat_id: "-100",
        secret: "1:abc",
      }),
    );
  });

  it("sends a test and shows why it failed; warns without a mail server", async () => {
    vi.mocked(api.getChannelCapabilities).mockResolvedValue({ engine: true, email: false });
    vi.mocked(api.testIntegration).mockResolvedValue({ delivered: false, error: "chat not found" });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Send test to Ops mail" }));
    expect(await screen.findByText("Not delivered: chat not found")).toBeInTheDocument();
    expect(await screen.findByText(/no mail server yet/)).toBeInTheDocument();
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
    expect(await screen.findByRole("link", { name: "4" })).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("tab", { name: "JSON" }));
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
    expect(vi.mocked(api.testRule).mock.calls[0][3]).toHaveLength(10);
  });

  it("builds a rule in the form, with metric search and channels", async () => {
    vi.mocked(api.createRule).mockResolvedValue({ id: "rule-9" } as api.Rule);
    editor();
    expect(await screen.findByTestId("live-matches")).toHaveTextContent(
      "1 device is over these limits right now: pump-1",
    );
    const picker = screen.getByRole("combobox", { name: "Condition 1 metric" });
    fireEvent.focus(picker);
    fireEvent.change(picker, { target: { value: "memory" } });
    expect(screen.getAllByRole("option", { name: /usage/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("option", { name: /Memory usage/ }));
    fireEvent.change(screen.getByLabelText("Condition 1 value"), { target: { value: "85" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Memory" } });
    fireEvent.change(screen.getByLabelText("Applies to"), { target: { value: "group" } });
    fireEvent.click(await screen.findByLabelText("Notify Ops mail"));
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    expect(await screen.findByText(/Choose a device group/)).toBeInTheDocument();
    expect(api.createRule).not.toHaveBeenCalled();
    await screen.findByRole("option", { name: "Stores" });
    fireEvent.change(screen.getByLabelText("Device group"), { target: { value: "grp-1" } });
    fireEvent.click(screen.getByLabelText("Activate the alarm as soon as it is created"));
    expect(screen.getByTestId("rule-summary")).toHaveTextContent(
      "When Memory usage is above 85%, for at least 5 minutes: open a warning alert and notify 1 channel.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(api.createRule).toHaveBeenCalled());
    expect(vi.mocked(api.createRule).mock.calls[0][2]).toEqual({
      schema_version: 1,
      name: "Memory",
      enabled: false,
      severity: "warning",
      scope: { type: "group", group_id: "grp-1" },
      condition: {
        type: "duration",
        duration: "5m",
        condition: { type: "comparison", field: "memory_percent", operator: "gt", value: 85 },
      },
      cooldown: "10m",
      actions: [
        { type: "alert" },
        { type: "notification", integration_id: "int-1", on: ["firing", "resolved"] },
      ],
    });
  });

  it("moves between form and JSON, and keeps rules the form cannot show in JSON", async () => {
    editor();
    fireEvent.click(screen.getByRole("tab", { name: "JSON" }));
    const box = screen.getByLabelText("Rule definition (JSON)") as HTMLTextAreaElement;
    const definition = JSON.parse(box.value);
    fireEvent.change(box, { target: { value: JSON.stringify({ ...definition, name: "Edited" }) } });
    fireEvent.click(screen.getByRole("tab", { name: "Form" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Edited");
    fireEvent.click(screen.getByRole("tab", { name: "JSON" }));
    const nested = { ...definition, condition: { type: "not", condition: definition.condition } };
    fireEvent.change(screen.getByLabelText("Rule definition (JSON)"), {
      target: { value: JSON.stringify(nested) },
    });
    fireEvent.click(screen.getByRole("tab", { name: "Form" }));
    expect(await screen.findByText(/features the form does not have/)).toBeInTheDocument();
    expect(screen.getByLabelText("Rule definition (JSON)")).toHaveValue(JSON.stringify(nested));
  });

  it("starts from a device health filter", async () => {
    renderAt(
      "/organizations/org-1/rules/new?c=memory_percent:gt:80:",
      "/organizations/:organizationId/rules/:ruleId",
      <RuleEditorPage />,
    );
    expect(screen.getByLabelText("Name")).toHaveValue("memory_percent is above 80");
  });

  it("saving a rule that is switched off keeps it off", async () => {
    vi.mocked(api.getRule).mockResolvedValue({
      id: "rule-1",
      name: "High CPU",
      current_version: 1,
      enabled: false,
      definition: {
        schema_version: 1,
        name: "High CPU",
        scope: { type: "organization" },
        condition: { type: "comparison", field: "cpu_percent", operator: "gt", value: 80 },
      },
    } as unknown as api.Rule);
    vi.mocked(api.updateRule).mockResolvedValue({ id: "rule-1" } as api.Rule);
    renderAt(
      "/organizations/org-1/rules/rule-1",
      "/organizations/:organizationId/rules/:ruleId",
      <RuleEditorPage />,
    );
    await screen.findByRole("heading", { name: "High CPU" });
    fireEvent.click(await screen.findByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(api.updateRule).toHaveBeenCalled());
    expect(vi.mocked(api.updateRule).mock.calls[0][3]).toMatchObject({ enabled: false });
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
