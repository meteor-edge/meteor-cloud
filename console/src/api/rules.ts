import { apiRequest } from "@/api/http";

/** A rule definition (contracts/rules-v1.md). Kept loose: the server validates it. */
export type RuleDefinition = Record<string, unknown>;

export type Rule = {
  id: string;
  organization_id: string | null;
  name: string;
  enabled: boolean;
  current_version: number;
  scope_type: string;
  severity: Severity;
  definition: RuleDefinition;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type RuleVersion = {
  version: number;
  definition: RuleDefinition;
  created_by_user_id: string | null;
  created_at: string;
};

export type Severity = "info" | "warning" | "critical";
export type AlertStatus = "firing" | "acknowledged" | "resolved";

export type Problem = { path: string; message: string };
export type ValidationResult = {
  valid: boolean;
  problems: Problem[];
  condition_hash: string | null;
};

export type DevicePreview = {
  total: number;
  devices: {
    id: string;
    name: string;
    device_group_id: string | null;
    device_type_id: string | null;
  }[];
};

export type Alert = {
  id: string;
  organization_id: string;
  rule_id: string;
  rule_version: number;
  rule_name: string;
  device_id: string;
  episode: number;
  fingerprint: string;
  severity: Severity;
  status: AlertStatus;
  silenced: boolean;
  fired_at: string;
  acknowledged_at: string | null;
  acknowledged_by_user_id: string | null;
  resolved_at: string | null;
  resolve_reason: string | null;
  trigger_event_id: string | null;
  trigger_values: Record<string, number>;
};

export type AlertEvent = {
  id: string;
  transition: string;
  actor_type: string;
  actor_id: string | null;
  at: string;
  reason: string | null;
  event_id: string | null;
  values: Record<string, number>;
};

export type AlertDetail = Alert & { events: AlertEvent[] };

export type AlertFilters = {
  status?: AlertStatus;
  active?: boolean;
  rule_id?: string;
  device_id?: string;
  severity?: Severity;
  before?: string;
  before_id?: string;
  limit?: number;
};

export type Silence = {
  id: string;
  organization_id: string;
  rule_id: string | null;
  device_id: string | null;
  comment: string | null;
  starts_at: string;
  ends_at: string;
  created_by_user_id: string | null;
  created_at: string;
};

export type Integration = {
  id: string;
  organization_id: string;
  kind: "webhook" | "slack";
  name: string;
  url: string;
  enabled: boolean;
  has_secret: boolean;
  created_at: string;
  updated_at: string;
};

export type TestEvent = {
  id?: string;
  time: string;
  stream?: string;
  data: Record<string, unknown>;
};

export type TestStep = {
  at: string;
  event_id?: string;
  timer?: boolean;
  phase: string;
  transitions?: { transition: string; reason?: string; episode: number }[];
  values?: Record<string, number>;
  skipped?: string;
};

export type RuleStatus = {
  rule_id: string;
  current_version: number;
  enabled: boolean;
  devices: {
    device_id: string;
    rule_version: number;
    phase: string;
    phase_since: string;
    episode: number;
    cooldown_until?: string;
    last_evaluated: string;
    last_event_id?: string;
    last_values?: Record<string, number>;
    timer_due?: string;
  }[];
};

function base(organizationId: string): string {
  return `/api/v1/organizations/${organizationId}`;
}

function query(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const listRules = (token: string, org: string) =>
  apiRequest<Rule[]>(`${base(org)}/rules?limit=200`, { token });

export const getRule = (token: string, org: string, id: string) =>
  apiRequest<Rule>(`${base(org)}/rules/${id}`, { token });

export const createRule = (token: string, org: string, definition: RuleDefinition) =>
  apiRequest<Rule>(`${base(org)}/rules`, { token, body: { definition } });

export const updateRule = (token: string, org: string, id: string, definition: RuleDefinition) =>
  apiRequest<Rule>(`${base(org)}/rules/${id}`, { token, method: "PUT", body: { definition } });

export const setRuleEnabled = (token: string, org: string, id: string, enabled: boolean) =>
  apiRequest<Rule>(`${base(org)}/rules/${id}/${enabled ? "enable" : "disable"}`, {
    token,
    method: "POST",
  });

export const deleteRule = (token: string, org: string, id: string) =>
  apiRequest<void>(`${base(org)}/rules/${id}`, { token, method: "DELETE" });

export const listRuleVersions = (token: string, org: string, id: string) =>
  apiRequest<RuleVersion[]>(`${base(org)}/rules/${id}/versions`, { token });

export const validateRule = (token: string, org: string, definition: RuleDefinition) =>
  apiRequest<ValidationResult>(`${base(org)}/rules/validate`, { token, body: { definition } });

export const previewDevices = (token: string, org: string, definition: RuleDefinition) =>
  apiRequest<DevicePreview>(`${base(org)}/rules/preview-devices`, { token, body: { definition } });

export const testRule = (
  token: string,
  org: string,
  definition: RuleDefinition,
  events: TestEvent[],
  until?: string,
) =>
  apiRequest<{ steps: TestStep[] }>(`${base(org)}/rules/test`, {
    token,
    body: { definition, events, ...(until ? { until } : {}) },
  });

export const getRuleStatus = (token: string, org: string, id: string) =>
  apiRequest<RuleStatus>(`${base(org)}/rules/${id}/status`, { token });

export const listAlerts = (token: string, org: string, filters: AlertFilters = {}) =>
  apiRequest<Alert[]>(`${base(org)}/alerts${query(filters)}`, { token });

export const getAlert = (token: string, org: string, id: string) =>
  apiRequest<AlertDetail>(`${base(org)}/alerts/${id}`, { token });

export const acknowledgeAlert = (token: string, org: string, id: string) =>
  apiRequest<Alert>(`${base(org)}/alerts/${id}/acknowledge`, { token, method: "POST" });

export const listSilences = (token: string, org: string) =>
  apiRequest<Silence[]>(`${base(org)}/alert-silences`, { token });

export const createSilence = (
  token: string,
  org: string,
  body: { rule_id?: string; device_id?: string; comment?: string; ends_at: string },
) => apiRequest<Silence>(`${base(org)}/alert-silences`, { token, body });

export const deleteSilence = (token: string, org: string, id: string) =>
  apiRequest<void>(`${base(org)}/alert-silences/${id}`, { token, method: "DELETE" });

export const listIntegrations = (token: string, org: string) =>
  apiRequest<Integration[]>(`${base(org)}/notification-integrations`, { token });

export const createIntegration = (
  token: string,
  org: string,
  body: { kind: "webhook" | "slack"; name: string; url: string; secret?: string },
) => apiRequest<Integration>(`${base(org)}/notification-integrations`, { token, body });

export const updateIntegration = (
  token: string,
  org: string,
  id: string,
  body: { name?: string; url?: string; enabled?: boolean; secret?: string },
) =>
  apiRequest<Integration>(`${base(org)}/notification-integrations/${id}`, {
    token,
    method: "PATCH",
    body,
  });

export const deleteIntegration = (token: string, org: string, id: string) =>
  apiRequest<void>(`${base(org)}/notification-integrations/${id}`, { token, method: "DELETE" });
