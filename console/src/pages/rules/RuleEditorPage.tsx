import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  createRule,
  deleteRule,
  getRule,
  getRuleStatus,
  listRuleVersions,
  previewDevices,
  setRuleEnabled,
  testRule,
  updateRule,
  validateRule,
  type DevicePreview,
  type Problem,
  type RuleDefinition,
  type TestEvent,
  type TestStep,
} from "@/api/rules";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { canEditRules } from "@/lib/permissions";

import { cardClass, errorText, formatValues, tableHeadClass, useOrgRole } from "./shared";

const RULE_TEMPLATE: RuleDefinition = {
  schema_version: 1,
  name: "High CPU",
  severity: "warning",
  scope: { type: "organization" },
  condition: {
    type: "duration",
    duration: "5m",
    condition: { type: "comparison", field: "cpu_percent", operator: "gt", value: 90 },
  },
  resolve: { type: "comparison", field: "cpu_percent", operator: "lt", value: 80 },
  cooldown: "10m",
};

function sampleEvents(): TestEvent[] {
  const start = Date.now() - 10 * 60_000;
  return [0, 2, 4, 6, 8].map((minute, index) => ({
    time: new Date(start + minute * 60_000).toISOString(),
    data: { cpu_percent: index < 4 ? 95 : 50 },
  }));
}

const KEY_ORDER = [
  "schema_version",
  "name",
  "description",
  "enabled",
  "severity",
  "scope",
  "source",
  "condition",
  "resolve",
  "cooldown",
  "alerting",
  "actions",
  "late_tolerance",
];

/** The server stores JSON without key order; show a definition in the order people read it. */
function ordered(definition: RuleDefinition): RuleDefinition {
  const keys = Object.keys(definition).sort((a, b) => {
    const [ia, ib] = [KEY_ORDER.indexOf(a), KEY_ORDER.indexOf(b)];
    return (ia < 0 ? KEY_ORDER.length : ia) - (ib < 0 ? KEY_ORDER.length : ib);
  });
  return Object.fromEntries(keys.map((key) => [key, definition[key]]));
}

const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const textareaClass =
  "mt-1 block w-full rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed";

function parse<T>(text: string): { value?: T; error?: string } {
  try {
    return { value: JSON.parse(text) as T };
  } catch (error) {
    return { error: error instanceof Error ? error.message : "Invalid JSON" };
  }
}

export function RuleEditorPage() {
  const { organizationId = "", ruleId } = useParams();
  const isNew = !ruleId || ruleId === "new";
  const { token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const canEdit = canEditRules(useOrgRole(organizationId));
  const enabled = Boolean(token && organizationId && !isNew);

  const rule = useQuery({
    queryKey: ["rule", organizationId, ruleId, token],
    queryFn: () => getRule(token!, organizationId, ruleId!),
    enabled,
  });
  const versions = useQuery({
    queryKey: ["rule-versions", organizationId, ruleId, token],
    queryFn: () => listRuleVersions(token!, organizationId, ruleId!),
    enabled,
  });
  const status = useQuery({
    queryKey: ["rule-status", organizationId, ruleId, token],
    queryFn: () => getRuleStatus(token!, organizationId, ruleId!),
    enabled,
    retry: false,
    refetchInterval: 15_000,
  });

  const [text, setText] = useState(() => pretty(RULE_TEMPLATE));
  const [eventsText, setEventsText] = useState(() => pretty(sampleEvents()));
  const [problems, setProblems] = useState<Problem[] | null>(null);
  const [preview, setPreview] = useState<DevicePreview | null>(null);
  const [steps, setSteps] = useState<TestStep[] | null>(null);
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    if (rule.data) setText(pretty(ordered(rule.data.definition)));
  }, [rule.data]);

  function definition(): RuleDefinition | undefined {
    const parsed = parse<RuleDefinition>(text);
    if (parsed.error) {
      setLocalError(`The definition is not valid JSON: ${parsed.error}`);
      return undefined;
    }
    setLocalError("");
    return parsed.value;
  }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["rules", organizationId] });
    queryClient.invalidateQueries({ queryKey: ["rule", organizationId, ruleId] });
    queryClient.invalidateQueries({ queryKey: ["rule-versions", organizationId, ruleId] });
  };
  const validate = useMutation({
    mutationFn: (def: RuleDefinition) => validateRule(token!, organizationId, def),
    onSuccess: (result) => setProblems(result.problems),
  });
  const previewing = useMutation({
    mutationFn: (def: RuleDefinition) => previewDevices(token!, organizationId, def),
    onSuccess: setPreview,
  });
  const testing = useMutation({
    mutationFn: ({ def, events }: { def: RuleDefinition; events: TestEvent[] }) =>
      testRule(token!, organizationId, def, events),
    onSuccess: (result) => setSteps(result.steps),
  });
  const save = useMutation({
    mutationFn: (def: RuleDefinition) =>
      isNew
        ? createRule(token!, organizationId, def)
        : updateRule(token!, organizationId, ruleId!, def),
    onSuccess: (saved) => {
      setProblems([]);
      invalidate();
      if (isNew) navigate(`/organizations/${organizationId}/rules/${saved.id}`);
    },
  });
  const toggle = useMutation({
    mutationFn: (on: boolean) => setRuleEnabled(token!, organizationId, ruleId!, on),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => deleteRule(token!, organizationId, ruleId!),
    onSuccess: () => {
      invalidate();
      navigate(`/organizations/${organizationId}/rules`);
    },
  });

  const actionError =
    save.error ?? validate.error ?? previewing.error ?? toggle.error ?? remove.error;

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link
            className="text-sm text-muted-foreground hover:underline"
            to={`/organizations/${organizationId}/rules`}
          >
            ← Rules
          </Link>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">
            {isNew ? "New rule" : (rule.data?.name ?? "Rule")}
          </h1>
          {rule.data && (
            <p className="mt-1 text-sm text-muted-foreground">
              Version {rule.data.current_version} · {rule.data.enabled ? "enabled" : "disabled"} ·{" "}
              <Link
                className="underline"
                to={`/organizations/${organizationId}/alerts?rule_id=${rule.data.id}`}
              >
                alerts
              </Link>
            </p>
          )}
        </div>
        {!isNew && rule.data && canEdit && (
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={toggle.isPending}
              onClick={() => toggle.mutate(!rule.data!.enabled)}
            >
              {rule.data.enabled ? "Disable" : "Enable"}
            </Button>
            <Button
              variant="ghost"
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm("Delete this rule? Its open alerts resolve; history stays."))
                  remove.mutate();
              }}
            >
              Delete
            </Button>
          </div>
        )}
      </div>

      <div className={`space-y-3 ${cardClass}`}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold">Definition</h2>
          <a
            className="text-sm text-muted-foreground underline"
            href="https://github.com/meteor-edge/meteor-cloud/blob/master/contracts/rules-v1.md"
            target="_blank"
            rel="noreferrer"
          >
            Rule format reference
          </a>
        </div>
        <label htmlFor="rule-definition" className="sr-only">
          Rule definition (JSON)
        </label>
        <textarea
          id="rule-definition"
          className={textareaClass}
          rows={22}
          spellCheck={false}
          value={text}
          readOnly={!canEdit}
          onChange={(event) => {
            setText(event.target.value);
            setProblems(null);
          }}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => {
              const d = definition();
              if (d) validate.mutate(d);
            }}
          >
            Validate
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              const d = definition();
              if (d) previewing.mutate(d);
            }}
          >
            Preview devices
          </Button>
          {canEdit && (
            <Button
              disabled={save.isPending}
              onClick={() => {
                const d = definition();
                if (d) save.mutate(d);
              }}
            >
              {isNew ? "Create rule" : "Save new version"}
            </Button>
          )}
        </div>
        {localError && (
          <p className="text-sm text-destructive" role="alert">
            {localError}
          </p>
        )}
        {actionError && (
          <p className="text-sm text-destructive" role="alert">
            {errorText(actionError, "The request failed.")}
          </p>
        )}
        {problems && problems.length === 0 && !actionError && (
          <p className="text-sm text-emerald-600">The rule is valid.</p>
        )}
        {problems && problems.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-sm text-destructive" aria-label="Problems">
            {problems.map((problem) => (
              <li key={`${problem.path}:${problem.message}`}>
                {problem.path && <code>{problem.path}</code>} {problem.message}
              </li>
            ))}
          </ul>
        )}
        {preview && (
          <p className="text-sm text-muted-foreground">
            Applies to {preview.total} device{preview.total === 1 ? "" : "s"} now
            {preview.devices.length > 0 &&
              `: ${preview.devices
                .slice(0, 10)
                .map((d) => d.name)
                .join(", ")}`}
            {preview.total > 10 && " …"}
          </p>
        )}
      </div>

      <div className={`space-y-3 ${cardClass}`}>
        <h2 className="text-lg font-semibold">Test with sample events</h2>
        <p className="text-sm text-muted-foreground">
          Runs the definition above over these events with a virtual clock. Nothing is stored and no
          action runs.
        </p>
        <label htmlFor="rule-events" className="sr-only">
          Sample events (JSON)
        </label>
        <textarea
          id="rule-events"
          className={textareaClass}
          rows={10}
          spellCheck={false}
          value={eventsText}
          onChange={(event) => setEventsText(event.target.value)}
        />
        <Button
          variant="outline"
          disabled={testing.isPending}
          onClick={() => {
            const def = definition();
            const events = parse<TestEvent[]>(eventsText);
            if (events.error) {
              setLocalError(`The sample events are not valid JSON: ${events.error}`);
              return;
            }
            if (def) testing.mutate({ def, events: events.value! });
          }}
        >
          Run test
        </Button>
        {testing.isError && (
          <p className="text-sm text-destructive" role="alert">
            {errorText(testing.error, "The test could not run.")}
          </p>
        )}
        {steps && (
          <table className="min-w-full text-left text-sm">
            <thead className={tableHeadClass}>
              <tr>
                <th className="px-3 py-2 font-semibold">At</th>
                <th className="px-3 py-2 font-semibold">Phase</th>
                <th className="px-3 py-2 font-semibold">Transitions</th>
                <th className="px-3 py-2 font-semibold">Values</th>
              </tr>
            </thead>
            <tbody>
              {steps.map((step, index) => (
                <tr key={index} className="border-b border-border last:border-0">
                  <td className="whitespace-nowrap px-3 py-2">
                    {new Date(step.at).toLocaleTimeString()}
                    {step.timer && <span className="text-xs text-muted-foreground"> (timer)</span>}
                  </td>
                  <td className="px-3 py-2">{step.phase}</td>
                  <td className="px-3 py-2 font-semibold">
                    {(step.transitions ?? [])
                      .map((t) => t.transition + (t.reason ? ` (${t.reason})` : ""))
                      .join(", ")}
                    {step.skipped && (
                      <span className="font-normal text-muted-foreground">
                        skipped: {step.skipped}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                    {formatValues(step.values)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!isNew && (
        <div className={`space-y-3 ${cardClass}`}>
          <h2 className="text-lg font-semibold">Evaluation status</h2>
          {status.isError && (
            <p className="text-sm text-muted-foreground">
              {errorText(status.error, "The rule engine is not reachable.")}
            </p>
          )}
          {status.data && status.data.devices.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No device has reported data this rule reads yet.
            </p>
          )}
          {status.data && status.data.devices.length > 0 && (
            <table className="min-w-full text-left text-sm">
              <thead className={tableHeadClass}>
                <tr>
                  <th className="px-3 py-2 font-semibold">Device</th>
                  <th className="px-3 py-2 font-semibold">Phase</th>
                  <th className="px-3 py-2 font-semibold">Since</th>
                  <th className="px-3 py-2 font-semibold">Last values</th>
                </tr>
              </thead>
              <tbody>
                {status.data.devices.map((device) => (
                  <tr key={device.device_id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 font-mono text-xs">
                      <Link
                        className="hover:underline"
                        to={`/organizations/${organizationId}/devices/${device.device_id}`}
                      >
                        {device.device_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="px-3 py-2">{device.phase}</td>
                    <td className="px-3 py-2">{new Date(device.phase_since).toLocaleString()}</td>
                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                      {formatValues(device.last_values)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {!isNew && (versions.data?.length ?? 0) > 0 && (
        <div className={`space-y-3 ${cardClass}`}>
          <h2 className="text-lg font-semibold">Versions</h2>
          <ul className="divide-y divide-border text-sm">
            {versions.data!.map((version) => (
              <li key={version.version} className="flex items-center justify-between py-2">
                <span>
                  Version {version.version} · {new Date(version.created_at).toLocaleString()}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setText(pretty(ordered(version.definition)))}
                >
                  Load into editor
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
