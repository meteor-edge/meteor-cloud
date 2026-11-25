import { describe, expect, it } from "vitest";

import {
  durationText,
  emptyForm,
  formProblems,
  fromDefinition,
  newCondition,
  sampleEvents,
  summary,
  TEMPLATES,
  toDefinition,
} from "./form";

// A glob, so the console still type-checks where only its own folder exists (image build).
const examples = Object.values(
  import.meta.glob<Record<string, unknown>[]>("../../../../contracts/rules-v1.examples.json", {
    eager: true,
    import: "default",
  }),
).flat();

const full = {
  schema_version: 1,
  name: "Pressure",
  enabled: false,
  severity: "critical",
  scope: { type: "group", group_id: "g1", labels: { env: "prod" } },
  source: { streams: ["meteor.device.metrics"] },
  condition: {
    type: "duration",
    duration: "7m",
    condition: {
      type: "any",
      conditions: [
        { type: "comparison", field: "cpu_percent", operator: "between", min: 80, max: 100 },
        {
          type: "aggregate",
          function: "avg",
          field: "app.web.mem",
          window: "10m",
          operator: "gte",
          value: 5,
        },
        { type: "absence", duration: "5m" },
      ],
    },
  },
  cooldown: "1h30m",
  actions: [
    { type: "alert", severity: "info" },
    { type: "notification", integration_id: "i1", on: ["resolved", "firing"] },
    { type: "webhook", integration_id: "i2" },
    { type: "webhook", integration_id: "i3", params: { a: 1 } },
    { type: "command", command: "reboot" },
  ],
};

describe("rule form conversion", () => {
  it("keeps everything of a rule it can show", () => {
    const form = fromDefinition(full)!;
    expect(form.conditions.map((c) => c.kind)).toEqual(["value", "aggregate", "absence"]);
    expect(form.channels.map((c) => c.integration_id)).toEqual(["i1", "i2"]);
    const again = toDefinition(form);
    expect(again).toMatchObject({ enabled: false, cooldown: "1h30m", source: full.source });
    expect(again.scope).toEqual(full.scope);
    expect(again.condition).toEqual(full.condition);
    expect(again.actions).toContainEqual({
      type: "webhook",
      integration_id: "i3",
      params: { a: 1 },
    });
    expect(again.actions).toContainEqual({ type: "command", command: "reboot" });
    expect(fromDefinition(again)).toEqual(form);
  });

  it("refuses what it cannot show instead of dropping it", () => {
    const base = {
      ...full,
      condition: { type: "comparison", field: "a", operator: "gt", value: 1 },
    };
    expect(fromDefinition(base)).not.toBeNull();
    for (const change of [
      { condition: { type: "not", condition: base.condition } },
      { condition: { type: "comparison", field: "a", operator: "eq", value: "text" } },
      { condition: { type: "comparison", field: "a", operator: "eq", value: true } },
      { condition: { type: "comparison", field: "a", operator: "in", values: [1] } },
      { condition: { type: "all", conditions: [base.condition] } },
      { condition: { type: "absence", duration: "5m", streams: ["x"] } },
      { resolve: { type: "comparison", field: "other", operator: "lt", value: 1 } },
      { resolve: { type: "comparison", field: "a", operator: "lte", value: 1 } },
      { scope: null },
    ])
      expect(fromDefinition({ ...base, ...change }), JSON.stringify(change)).toBeNull();
  });

  it.each(examples.map((e) => [e.name, e] as [string, Record<string, unknown>]))(
    "contract example %s is shown losslessly or left to JSON",
    (_name, example) => {
      const form = fromDefinition(example);
      if (form) expect(fromDefinition(toDefinition(form))).toEqual(form);
    },
  );

  it("builds a resolve threshold on the healthy side", () => {
    const form = emptyForm({ resolveValue: "70" });
    expect(toDefinition(form).resolve).toEqual({
      type: "comparison",
      field: "cpu_percent",
      operator: "lt",
      value: 70,
    });
    expect(fromDefinition(toDefinition(form))).toEqual(form);
    expect(formProblems({ ...form, resolveValue: "85" })).toHaveLength(1);
    expect(
      toDefinition({ ...form, conditions: [newCondition(), newCondition()] }).resolve,
    ).toBeUndefined();
  });

  it("every template is complete and survives the JSON view", () => {
    for (const t of TEMPLATES) {
      const form = emptyForm(t.form);
      expect(formProblems(form)).toEqual([]);
      expect(fromDefinition(toDefinition(form))).toEqual(form);
    }
  });

  it("names what is missing", () => {
    expect(
      formProblems(
        emptyForm({
          name: " ",
          alert: null,
          scope: { type: "device", group_id: "", device_type_id: "", device_ids: [] },
          conditions: [
            newCondition({ field: "", value: "" }),
            newCondition({ operator: "between", value: "9", max: "1" }),
          ],
        }),
      ),
    ).toHaveLength(6);
  });

  it("says the rule in words and makes events that break it", () => {
    const form = emptyForm();
    expect(summary(form, {})).toBe(
      "When cpu_percent is above 80, for at least 5 minutes: open a warning alert.",
    );
    expect(durationText("90s")).toBe("90 seconds");
    const events = sampleEvents(form, Date.parse("2026-11-24T12:00:00Z"));
    expect(events.filter((e) => Number(e.data.cpu_percent) > 80)).toHaveLength(6);
    const span = Date.parse(events[6].time) - Date.parse(events[1].time);
    expect(span).toBeGreaterThan(5 * 60_000);
  });
});
