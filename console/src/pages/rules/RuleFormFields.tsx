import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listDeviceGroups } from "@/api/fleet/deviceGroups";
import { listDeviceTypes } from "@/api/fleet/deviceTypes";
import {
  getChannelCapabilities,
  listIntegrations,
  queryLiveDevices,
  type LiveQuery,
  type Severity,
} from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

import { KIND_LABEL } from "./channelKinds";
import { ConditionRow, DurationSelect } from "./ConditionRow";
import { DevicePicker } from "./DevicePicker";
import {
  canSetResolve,
  emptyForm,
  newCondition,
  summary,
  TEMPLATES,
  type FormScope,
  type RuleForm,
} from "./form";
import { selectClass, useDebounced, useMetrics } from "./shared";

/** Current readings the conditions can be checked against; null when there are none. */
function liveQuery(form: RuleForm): LiveQuery | null {
  const usable = form.conditions.filter((c) => c.kind !== "absence" && c.field);
  if (usable.length !== form.conditions.length) return null;
  if (
    usable.some(
      (c) =>
        c.value.trim() === "" ||
        (c.operator === "between" && (c.max.trim() === "" || Number(c.max) < Number(c.value))) ||
        (c.kind === "aggregate" && ["delta", "rate", "sum", "count"].includes(c.fn)),
    )
  )
    return null;
  if (form.scope.type === "group" && !form.scope.group_id) return null;
  if (form.scope.type === "device_type" && !form.scope.device_type_id) return null;
  return {
    match: form.match,
    conditions: usable.map((c) => ({
      field: c.field,
      operator: c.operator,
      value: Number(c.value),
      ...(c.operator === "between" ? { max: Number(c.max) } : {}),
    })),
    ...(form.scope.type === "group" ? { group_id: form.scope.group_id } : {}),
    ...(form.scope.type === "device_type" ? { device_type_id: form.scope.device_type_id } : {}),
    limit: 500,
  };
}

function LiveMatches({ organizationId, form }: { organizationId: string; form: RuleForm }) {
  const { token } = useAuth();
  const query = useDebounced(liveQuery(form));
  const live = useQuery({
    queryKey: ["live-devices", organizationId, token, query],
    queryFn: () => queryLiveDevices(token!, organizationId, query!),
    enabled: Boolean(token && query),
    refetchInterval: 30_000,
  });
  if (!query || !live.data) return null;
  const devices =
    form.scope.type === "device"
      ? live.data.devices.filter((d) => form.scope.device_ids.includes(d.id))
      : live.data.devices;
  const count = form.scope.type === "device" ? devices.length : live.data.matching;
  return (
    <p className="text-sm text-muted-foreground" data-testid="live-matches">
      {count === 0
        ? "No device is over these limits right now."
        : `${count} device${count === 1 ? " is" : "s are"} over these limits right now: `}
      {devices
        .slice(0, 5)
        .map((d) => d.name)
        .join(", ")}
      {count > 5 && " …"}
      {form.scope.labels !== undefined && " (device labels are not considered here)"}
    </p>
  );
}

export function RuleFormFields({
  organizationId,
  form,
  onChange,
  isNew,
  disabled,
}: {
  organizationId: string;
  form: RuleForm;
  onChange: (form: RuleForm) => void;
  isNew: boolean;
  disabled?: boolean;
}) {
  const { token } = useAuth();
  const enabled = Boolean(token && organizationId);
  const metrics = useMetrics(organizationId);
  const groups = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token!, organizationId),
    enabled: enabled && form.scope.type === "group",
  });
  const types = useQuery({
    queryKey: ["device-types", organizationId, token],
    queryFn: () => listDeviceTypes(token!, organizationId),
    enabled: enabled && form.scope.type === "device_type",
  });
  const integrations = useQuery({
    queryKey: ["notification-integrations", organizationId, token],
    queryFn: () => listIntegrations(token!, organizationId),
    enabled,
  });
  const capabilities = useQuery({
    queryKey: ["channel-capabilities", organizationId, token],
    queryFn: () => getChannelCapabilities(token!, organizationId),
    enabled,
  });
  const set = (over: Partial<RuleForm>) => onChange({ ...form, ...over });
  const setScope = (over: Partial<FormScope>) => set({ scope: { ...form.scope, ...over } });
  // A channel that was deleted still shows, so it can be cleared.
  const known = integrations.data ?? [];
  const gone = form.channels.filter((c) => !known.some((i) => i.id === c.integration_id));

  return (
    <div className="space-y-6">
      {isNew && !disabled && (
        <div>
          <p className="text-sm font-semibold">Start from</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {TEMPLATES.map((t) => (
              <button
                key={t.id}
                type="button"
                className="rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-secondary"
                onClick={() =>
                  onChange(
                    emptyForm({
                      ...t.form,
                      scope: form.scope,
                      channels: form.channels,
                      rest: form.rest,
                    }),
                  )
                }
              >
                <span className="block font-medium">{t.label}</span>
                <span className="text-xs text-muted-foreground">{t.hint}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-3">
        <div>
          <Label htmlFor="rule-name">Name</Label>
          <Input
            id="rule-name"
            className="mt-1"
            value={form.name}
            maxLength={200}
            disabled={disabled}
            onChange={(e) => set({ name: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="rule-severity">Severity</Label>
          <select
            id="rule-severity"
            className={selectClass}
            value={form.severity}
            disabled={disabled}
            onChange={(e) => set({ severity: e.target.value as Severity })}
          >
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
        </div>
        <div>
          <Label htmlFor="rule-description">Description</Label>
          <Input
            id="rule-description"
            className="mt-1"
            placeholder="Optional"
            value={form.description}
            maxLength={2000}
            disabled={disabled}
            onChange={(e) => set({ description: e.target.value })}
          />
        </div>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold">Which devices</legend>
        <div className="grid gap-3 md:grid-cols-3">
          <select
            aria-label="Applies to"
            className={selectClass}
            value={form.scope.type}
            disabled={disabled}
            onChange={(e) => setScope({ type: e.target.value as FormScope["type"] })}
          >
            <option value="organization">All devices</option>
            <option value="group">A device group</option>
            <option value="device_type">A device type</option>
            <option value="device">Chosen devices</option>
          </select>
          {form.scope.type === "group" && (
            <select
              aria-label="Device group"
              className={selectClass}
              value={form.scope.group_id}
              disabled={disabled}
              onChange={(e) => setScope({ group_id: e.target.value })}
            >
              <option value="">Choose a group…</option>
              {(groups.data ?? []).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          )}
          {form.scope.type === "device_type" && (
            <select
              aria-label="Device type"
              className={selectClass}
              value={form.scope.device_type_id}
              disabled={disabled}
              onChange={(e) => setScope({ device_type_id: e.target.value })}
            >
              <option value="">Choose a type…</option>
              {(types.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
          {form.scope.type === "device" && (
            <div className="md:col-span-2">
              <DevicePicker
                organizationId={organizationId}
                label="Devices"
                value={form.scope.device_ids}
                disabled={disabled}
                onChange={(device_ids) => setScope({ device_ids })}
              />
            </div>
          )}
        </div>
        {form.scope.labels !== undefined && (
          <p className="text-xs text-muted-foreground">
            Only devices with labels <code>{JSON.stringify(form.scope.labels)}</code> (change in
            JSON).
          </p>
        )}
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold">Alarm when</legend>
        {form.conditions.length > 1 && (
          <select
            aria-label="Match"
            className={cn(selectClass, "max-w-64")}
            value={form.match}
            disabled={disabled}
            onChange={(e) => set({ match: e.target.value as RuleForm["match"] })}
          >
            <option value="all">All conditions are true</option>
            <option value="any">Any condition is true</option>
          </select>
        )}
        {form.conditions.map((condition, index) => (
          <ConditionRow
            key={index}
            index={index}
            condition={condition}
            metrics={metrics.list}
            disabled={disabled}
            onChange={(next) =>
              set({ conditions: form.conditions.map((c, i) => (i === index ? next : c)) })
            }
            onRemove={
              form.conditions.length > 1
                ? () => set({ conditions: form.conditions.filter((_, i) => i !== index) })
                : undefined
            }
          />
        ))}
        {!disabled && form.conditions.length < 20 && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              set({ conditions: [...form.conditions, newCondition({ field: "memory_percent" })] })
            }
          >
            Add condition
          </Button>
        )}
        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <Label htmlFor="rule-hold">It must last</Label>
            <div className="mt-1">
              <DurationSelect
                id="rule-hold"
                label="It must last"
                none="Alarm at once"
                value={form.hold}
                disabled={disabled}
                onChange={(hold) => set({ hold })}
              />
            </div>
          </div>
          {canSetResolve(form) && (
            <div>
              <Label htmlFor="rule-resolve">
                Resolve when it is{" "}
                {["gt", "gte"].includes(form.conditions[0].operator) ? "below" : "above"}
              </Label>
              <Input
                id="rule-resolve"
                className="mt-1"
                type="number"
                step="any"
                placeholder="The alarm threshold"
                value={form.resolveValue}
                disabled={disabled}
                onChange={(e) => set({ resolveValue: e.target.value })}
              />
            </div>
          )}
          <div>
            <Label htmlFor="rule-cooldown">After it resolves, wait</Label>
            <div className="mt-1">
              <DurationSelect
                id="rule-cooldown"
                label="After it resolves, wait"
                none="Do not wait"
                value={form.cooldown}
                disabled={disabled}
                onChange={(cooldown) => set({ cooldown })}
              />
            </div>
          </div>
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold">Then</legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.alert !== null}
            disabled={disabled}
            onChange={(e) => set({ alert: e.target.checked ? { type: "alert" } : null })}
          />
          Open an alert, and resolve it when it is over
        </label>
        {[
          ...known.map((i) => ({
            id: i.id,
            name: i.name,
            kind: KIND_LABEL[i.kind],
            type: i.kind === "webhook" ? "webhook" : "notification",
            note: !i.enabled
              ? "switched off"
              : i.kind === "email" && capabilities.data?.engine && !capabilities.data.email
                ? "this platform has no mail server"
                : "",
          })),
          ...gone.map((c) => ({
            id: c.integration_id,
            name: c.integration_id.slice(0, 8),
            kind: "Deleted channel",
            type: c.type,
            note: "no longer exists",
          })),
        ]
          .filter((i) => integrations.isSuccess || known.length > 0 || i.kind !== "Deleted channel")
          .map((item) => {
            const channel = form.channels.find((c) => c.integration_id === item.id);
            const update = (on: string[] | null) =>
              set({
                channels: on
                  ? channel
                    ? form.channels.map((c) => (c === channel ? { ...c, on } : c))
                    : [...form.channels, { integration_id: item.id, type: item.type, on }]
                  : form.channels.filter((c) => c !== channel),
              });
            return (
              <div key={item.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`Notify ${item.name}`}
                    checked={Boolean(channel)}
                    disabled={disabled}
                    onChange={(e) => update(e.target.checked ? ["firing", "resolved"] : null)}
                  />
                  Notify <span className="font-medium">{item.name}</span>
                  <span className="text-muted-foreground">
                    {item.kind}
                    {item.note && ` · ${item.note}`}
                  </span>
                </label>
                {channel &&
                  ["firing", "resolved"].map((when) => (
                    <label key={when} className="flex items-center gap-1 text-xs">
                      <input
                        type="checkbox"
                        aria-label={`Notify ${item.name} when ${when}`}
                        checked={channel.on.includes(when)}
                        disabled={disabled}
                        onChange={(e) =>
                          update(
                            e.target.checked
                              ? [...channel.on, when]
                              : channel.on.filter((w) => w !== when),
                          )
                        }
                      />
                      when {when}
                    </label>
                  ))}
              </div>
            );
          })}
        {integrations.isSuccess && known.length === 0 && (
          <p className="text-sm text-muted-foreground">
            To be notified,{" "}
            <Link className="underline" to={`/organizations/${organizationId}/channels`}>
              add an email, Telegram, Slack or webhook channel
            </Link>
            .
          </p>
        )}
        {form.otherActions.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Also: {form.otherActions.map((a) => String(a.type)).join(", ")} (change in JSON).
          </p>
        )}
      </fieldset>

      <div className="rounded-md border border-border bg-secondary p-3">
        <p className="text-sm font-medium" data-testid="rule-summary">
          {summary(form, metrics.byField)}
        </p>
        <LiveMatches organizationId={organizationId} form={form} />
      </div>
    </div>
  );
}
