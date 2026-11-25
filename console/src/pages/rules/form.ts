import type { Metric, RuleDefinition, Severity, TestEvent } from "@/api/rules";

/** What the form can express. Everything it cannot is kept as is (rest, otherActions). */
export type FormOperator = "gt" | "gte" | "lt" | "lte" | "eq" | "ne" | "between";
export type FormCondition = {
  kind: "value" | "aggregate" | "absence";
  field: string;
  operator: FormOperator;
  value: string;
  max: string;
  fn: string;
  window: string;
};
export type FormChannel = { integration_id: string; type: string; on: string[] };
export type FormScope = {
  type: "organization" | "group" | "device_type" | "device";
  group_id: string;
  device_type_id: string;
  device_ids: string[];
  labels?: unknown;
};
export type RuleForm = {
  name: string;
  description: string;
  severity: Severity;
  scope: FormScope;
  match: "all" | "any";
  conditions: FormCondition[];
  hold: string; // "" fires at once
  resolveValue: string; // "" resolves when the condition ends
  cooldown: string; // "" none
  channels: FormChannel[];
  alert: Record<string, unknown> | null;
  otherActions: Record<string, unknown>[];
  rest: Record<string, unknown>;
};

export const OPERATORS: { id: FormOperator; label: string; symbol: string }[] = [
  { id: "gt", label: "is above", symbol: ">" },
  { id: "gte", label: "is at or above", symbol: "≥" },
  { id: "lt", label: "is below", symbol: "<" },
  { id: "lte", label: "is at or below", symbol: "≤" },
  { id: "eq", label: "equals", symbol: "=" },
  { id: "ne", label: "is not", symbol: "≠" },
  { id: "between", label: "is between", symbol: "between" },
];
export const FUNCTIONS = [
  { id: "avg", label: "Average" },
  { id: "max", label: "Highest" },
  { id: "min", label: "Lowest" },
  { id: "delta", label: "Change" },
  { id: "rate", label: "Change per second" },
  { id: "sum", label: "Sum" },
  { id: "count", label: "Number of readings" },
];
const OPPOSITE: Partial<Record<FormOperator, "gt" | "lt">> = {
  gt: "lt",
  gte: "lt",
  lt: "gt",
  lte: "gt",
};

export const newCondition = (over: Partial<FormCondition> = {}): FormCondition => ({
  kind: "value",
  field: "cpu_percent",
  operator: "gt",
  value: "80",
  max: "",
  fn: "avg",
  window: "5m",
  ...over,
});

export const emptyForm = (over: Partial<RuleForm> = {}): RuleForm => ({
  name: "High CPU",
  description: "",
  severity: "warning",
  scope: { type: "organization", group_id: "", device_type_id: "", device_ids: [] },
  match: "all",
  conditions: [newCondition()],
  hold: "5m",
  resolveValue: "",
  cooldown: "10m",
  channels: [],
  alert: { type: "alert" },
  otherActions: [],
  rest: {},
  ...over,
});

export const TEMPLATES: { id: string; label: string; hint: string; form: Partial<RuleForm> }[] = [
  { id: "cpu", label: "High CPU", hint: "above 80% for 5 minutes", form: {} },
  {
    id: "memory",
    label: "High memory",
    hint: "above 80% for 5 minutes",
    form: { name: "High memory", conditions: [newCondition({ field: "memory_percent" })] },
  },
  {
    id: "disk",
    label: "Disk almost full",
    hint: "above 90%",
    form: {
      name: "Disk almost full",
      severity: "critical",
      conditions: [newCondition({ field: "disk_percent", value: "90" })],
      hold: "",
    },
  },
  {
    id: "temperature",
    label: "Overheating",
    hint: "above 75 °C for 2 minutes",
    form: {
      name: "Overheating",
      severity: "critical",
      conditions: [newCondition({ field: "temperature_c", value: "75" })],
      hold: "2m",
      resolveValue: "70",
    },
  },
  {
    id: "offline",
    label: "Device offline",
    hint: "no data for 5 minutes",
    form: {
      name: "Device offline",
      severity: "critical",
      conditions: [newCondition({ kind: "absence" })],
      hold: "",
      cooldown: "",
    },
  },
  {
    id: "pressure",
    label: "CPU or memory pressure",
    hint: "either above 80%",
    form: {
      name: "CPU or memory pressure",
      match: "any",
      conditions: [newCondition(), newCondition({ field: "memory_percent" })],
    },
  },
];

const num = (text: string): number => Number(text);
const isNumber = (text: string) => text.trim() !== "" && Number.isFinite(Number(text));

/** The single condition an auto-resolve threshold can apply to. */
export function canSetResolve(form: RuleForm): boolean {
  const [only] = form.conditions;
  return (
    form.conditions.length === 1 && only.kind !== "absence" && Boolean(OPPOSITE[only.operator])
  );
}

function comparison(c: FormCondition): Record<string, unknown> {
  const limits =
    c.operator === "between" ? { min: num(c.value), max: num(c.max) } : { value: num(c.value) };
  if (c.kind === "absence") return { type: "absence", duration: c.window };
  if (c.kind === "aggregate")
    return {
      type: "aggregate",
      function: c.fn,
      field: c.field,
      window: c.window,
      operator: c.operator,
      ...limits,
    };
  return { type: "comparison", field: c.field, operator: c.operator, ...limits };
}

export function toDefinition(form: RuleForm): RuleDefinition {
  const nodes = form.conditions.map(comparison);
  let condition: Record<string, unknown> =
    nodes.length === 1 ? nodes[0] : { type: form.match, conditions: nodes };
  if (form.hold) condition = { type: "duration", duration: form.hold, condition };
  const scope: Record<string, unknown> = { type: form.scope.type };
  if (form.scope.type === "group") scope.group_id = form.scope.group_id;
  if (form.scope.type === "device_type") scope.device_type_id = form.scope.device_type_id;
  if (form.scope.type === "device") scope.device_ids = form.scope.device_ids;
  if (form.scope.labels !== undefined) scope.labels = form.scope.labels;
  const actions = [
    ...(form.alert ? [form.alert] : []),
    ...form.channels.map((c) => ({ type: c.type, integration_id: c.integration_id, on: c.on })),
    ...form.otherActions,
  ];
  const only = form.conditions[0];
  return {
    schema_version: 1,
    name: form.name.trim(),
    ...(form.description.trim() ? { description: form.description.trim() } : {}),
    severity: form.severity,
    scope,
    condition,
    ...(form.resolveValue !== "" && canSetResolve(form)
      ? {
          resolve: {
            ...comparison({ ...only, operator: OPPOSITE[only.operator]! }),
            value: num(form.resolveValue),
          },
        }
      : {}),
    ...(form.cooldown ? { cooldown: form.cooldown } : {}),
    actions,
    ...form.rest,
  };
}

/** What is missing or wrong in the form, in words. The server validates again. */
export function formProblems(form: RuleForm): string[] {
  const out: string[] = [];
  if (!form.name.trim()) out.push("Give the rule a name.");
  if (form.scope.type === "group" && !form.scope.group_id) out.push("Choose a device group.");
  if (form.scope.type === "device_type" && !form.scope.device_type_id)
    out.push("Choose a device type.");
  if (form.scope.type === "device" && form.scope.device_ids.length === 0)
    out.push("Choose at least one device.");
  form.conditions.forEach((c, i) => {
    const at = form.conditions.length > 1 ? `Condition ${i + 1}: ` : "";
    if (c.kind === "absence") return;
    if (!c.field) out.push(`${at}choose a metric.`);
    if (!isNumber(c.value)) out.push(`${at}enter a number to compare with.`);
    if (c.operator === "between") {
      if (!isNumber(c.max)) out.push(`${at}enter the upper limit.`);
      else if (isNumber(c.value) && num(c.max) < num(c.value))
        out.push(`${at}the upper limit is below the lower one.`);
    }
  });
  if (form.resolveValue !== "" && canSetResolve(form)) {
    const c = form.conditions[0];
    if (!isNumber(form.resolveValue)) out.push("The resolve threshold must be a number.");
    else if (
      isNumber(c.value) &&
      (OPPOSITE[c.operator] === "lt"
        ? num(form.resolveValue) > num(c.value)
        : num(form.resolveValue) < num(c.value))
    )
      out.push("The resolve threshold must be on the healthy side of the alarm threshold.");
  }
  if (!form.alert && form.channels.length + form.otherActions.length === 0)
    out.push("Open an alert or choose a channel, otherwise the rule does nothing.");
  if (form.channels.some((c) => c.on.length === 0))
    out.push("Choose when each channel is notified, or clear it.");
  return out;
}

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function parseCondition(node: unknown): FormCondition | null {
  if (!isObject(node)) return null;
  const limits = {
    operator: node.operator as FormOperator,
    value: String(node.operator === "between" ? node.min : node.value),
    max: node.max === undefined ? "" : String(node.max),
  };
  if (node.type === "absence")
    return newCondition({ kind: "absence", window: String(node.duration) });
  if (node.type === "comparison")
    return newCondition({ kind: "value", field: String(node.field), ...limits });
  if (node.type === "aggregate")
    return newCondition({
      kind: "aggregate",
      field: String(node.field),
      fn: String(node.function),
      window: String(node.window),
      ...limits,
    });
  return null;
}

const defaultOn = (action: Json) => (Array.isArray(action.on) ? action.on : ["firing"]);

/** Definitions compared with defaults filled in and keys sorted. */
function canonical(definition: Json): string {
  const filled: Json = { ...definition };
  filled.severity ??= "warning";
  if (filled.cooldown === "0s" || filled.cooldown === "") delete filled.cooldown;
  if (filled.description === "") delete filled.description;
  const actions =
    Array.isArray(filled.actions) && filled.actions.length > 0
      ? filled.actions
      : [{ type: "alert" }];
  filled.actions = actions
    .map((a) =>
      isObject(a) && (a.type === "notification" || a.type === "webhook")
        ? { ...a, on: [...defaultOn(a)].sort() }
        : a,
    )
    .map((a) => JSON.stringify(sorted(a)))
    .sort();
  return JSON.stringify(sorted(filled));
}
function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (isObject(value))
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, sorted(value[k])]),
    );
  return value;
}

/** The form for a definition, or null when the form cannot express all of it. */
export function fromDefinition(definition: RuleDefinition): RuleForm | null {
  if (!isObject(definition) || !isObject(definition.scope)) return null;
  let root: unknown = definition.condition;
  let hold = "";
  if (isObject(root) && root.type === "duration") {
    hold = String(root.duration);
    root = root.condition;
  }
  let match: "all" | "any" = "all";
  let nodes: unknown[] = [root];
  if (isObject(root) && (root.type === "all" || root.type === "any")) {
    match = root.type;
    nodes = Array.isArray(root.conditions) ? root.conditions : [];
  }
  const conditions = nodes.map(parseCondition);
  if (conditions.length === 0 || conditions.some((c) => c === null)) return null;

  const {
    schema_version: _version,
    name,
    description,
    severity,
    scope,
    condition: _condition,
    resolve,
    cooldown,
    actions,
    ...rest
  } = definition;
  void _version;
  void _condition;
  const list = (
    Array.isArray(actions) && actions.length > 0 ? actions : [{ type: "alert" }]
  ).filter(isObject);
  const channels: FormChannel[] = [];
  const otherActions: Json[] = [];
  let alert: Json | null = null;
  for (const action of list) {
    const simple = Object.keys(action).every((k) => ["type", "integration_id", "on"].includes(k));
    if (action.type === "alert" && !alert) alert = action;
    else if ((action.type === "notification" || action.type === "webhook") && simple)
      channels.push({
        integration_id: String(action.integration_id),
        type: action.type,
        on: defaultOn(action) as string[],
      });
    else otherActions.push(action);
  }
  const s = scope as Json;
  const form: RuleForm = {
    name: String(name ?? ""),
    description: String(description ?? ""),
    severity: (severity as Severity) ?? "warning",
    scope: {
      type: s.type as FormScope["type"],
      group_id: String(s.group_id ?? ""),
      device_type_id: String(s.device_type_id ?? ""),
      device_ids: Array.isArray(s.device_ids) ? s.device_ids.map(String) : [],
      ...(s.labels !== undefined ? { labels: s.labels } : {}),
    },
    match,
    conditions: conditions as FormCondition[],
    hold,
    resolveValue: isObject(resolve) && resolve.value !== undefined ? String(resolve.value) : "",
    cooldown: cooldown && cooldown !== "0s" ? String(cooldown) : "",
    channels,
    alert,
    otherActions,
    rest,
  };
  // The form is only offered when saving it unchanged would give the same rule back.
  return canonical(toDefinition(form)) === canonical(definition) ? form : null;
}

// ---- words ----------------------------------------------------------------

export function durationSeconds(text: string): number {
  const unit: Record<string, number> = { ms: 0.001, s: 1, m: 60, h: 3600 };
  return [...text.matchAll(/(\d+(?:\.\d+)?)(ms|s|m|h)/g)].reduce(
    (sum, [, n, u]) => sum + Number(n) * unit[u],
    0,
  );
}

export function durationText(text: string): string {
  const s = durationSeconds(text);
  const say = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;
  if (s >= 3600 && s % 3600 === 0) return say(s / 3600, "hour");
  if (s >= 60 && s % 60 === 0) return say(s / 60, "minute");
  return say(s, "second");
}

export type MetricMap = Record<string, Metric>;

export const withUnit = (value: number | string, unit: string | undefined) =>
  unit ? `${value}${unit === "%" ? "" : " "}${unit}` : String(value);

export function conditionText(c: FormCondition, metrics: MetricMap): string {
  if (c.kind === "absence") return `the device sends no data for ${durationText(c.window)}`;
  const metric = metrics[c.field];
  const name = metric?.label ?? (c.field || "a metric");
  const limit =
    c.operator === "between"
      ? `${c.value || "…"} and ${withUnit(c.max || "…", metric?.unit)}`
      : withUnit(c.value || "…", metric?.unit);
  const operator = OPERATORS.find((o) => o.id === c.operator)?.label ?? c.operator;
  if (c.kind === "aggregate") {
    const fn = FUNCTIONS.find((f) => f.id === c.fn)?.label ?? c.fn;
    return `${fn} of ${name} over ${durationText(c.window)} ${operator} ${limit}`;
  }
  return `${name} ${operator} ${limit}`;
}

export function summary(form: RuleForm, metrics: MetricMap): string {
  const joined = form.conditions
    .map((c) => conditionText(c, metrics))
    .join(form.match === "all" ? " and " : " or ");
  const hold = form.hold ? `, for at least ${durationText(form.hold)}` : "";
  const what = [
    form.alert ? `open a ${form.severity} alert` : "",
    form.channels.length
      ? `notify ${form.channels.length} channel${form.channels.length === 1 ? "" : "s"}`
      : "",
    form.otherActions.length ? "run other actions" : "",
  ]
    .filter(Boolean)
    .join(" and ");
  return `When ${joined}${hold}: ${what || "do nothing"}.`;
}

// ---- sample data for the test panel ----------------------------------------

/** Readings that break the first limits long enough to fire, then recover. */
export function sampleEvents(form: RuleForm, now = Date.now()): TestEvent[] {
  const longest = Math.max(
    durationSeconds(form.hold || "0s"),
    ...form.conditions.map((c) => (c.kind === "value" ? 0 : durationSeconds(c.window))),
  );
  const step = Math.max(60, Math.ceil(longest / 4));
  const reading = (c: FormCondition, bad: boolean): number => {
    const v = num(c.value) || 0;
    const gap = Math.max(1, Math.abs(v) * 0.1);
    switch (c.operator) {
      case "lt":
      case "lte":
        return bad ? v - gap : v + 3 * gap;
      case "eq":
        return bad ? v : v + gap;
      case "ne":
        return bad ? v + gap : v;
      case "between":
        return bad ? (v + num(c.max)) / 2 : num(c.max) + gap;
      default:
        return bad ? v + gap : v - 3 * gap;
    }
  };
  const fields = form.conditions.filter((c) => c.kind !== "absence");
  const round = (n: number) => Math.round(n * 100) / 100;
  return Array.from({ length: 10 }, (_, i) => ({
    time: new Date(now - (10 - i) * step * 1000).toISOString(),
    data: fields.length
      ? Object.fromEntries(fields.map((c) => [c.field, round(reading(c, i >= 1 && i < 7))]))
      : { cpu_percent: 10 },
  }));
}
