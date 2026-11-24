import type { Metric } from "@/api/rules";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { durationText, FUNCTIONS, OPERATORS, type FormCondition, type FormOperator } from "./form";
import { MetricPicker } from "./MetricPicker";
import { selectClass } from "./shared";

const DURATIONS = ["30s", "1m", "2m", "5m", "10m", "15m", "30m", "1h", "2h", "6h", "12h", "24h"];

export function DurationSelect({
  id,
  label,
  value,
  onChange,
  none,
  disabled,
}: {
  id?: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  none?: string;
  disabled?: boolean;
}) {
  const options = value && !DURATIONS.includes(value) ? [...DURATIONS, value] : DURATIONS;
  return (
    <select
      id={id}
      aria-label={label}
      className={selectClass.replace("mt-1 ", "")}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {none !== undefined && <option value="">{none}</option>}
      {options.map((d) => (
        <option key={d} value={d}>
          {durationText(d)}
        </option>
      ))}
    </select>
  );
}

const plain = selectClass.replace("mt-1 ", "");

/** One condition. Without kinds, only a current reading can be compared. */
export function ConditionRow({
  index,
  condition,
  metrics,
  kinds = true,
  disabled,
  onChange,
  onRemove,
}: {
  index: number;
  condition: FormCondition;
  metrics: Metric[];
  kinds?: boolean;
  disabled?: boolean;
  onChange: (condition: FormCondition) => void;
  onRemove?: () => void;
}) {
  const n = index + 1;
  const set = (over: Partial<FormCondition>) => onChange({ ...condition, ...over });
  const unit = metrics.find((m) => m.field === condition.field)?.unit;
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-md border border-border p-3"
      role="group"
      aria-label={`Condition ${n}`}
    >
      {kinds && (
        <div className="w-full sm:w-44">
          <select
            aria-label={`Condition ${n} type`}
            className={plain}
            value={condition.kind}
            disabled={disabled}
            onChange={(e) => set({ kind: e.target.value as FormCondition["kind"] })}
          >
            <option value="value">Current value</option>
            <option value="aggregate">Over a time window</option>
            <option value="absence">No data received</option>
          </select>
        </div>
      )}
      {condition.kind === "absence" ? (
        <>
          <span className="text-sm">for</span>
          <div className="w-40">
            <DurationSelect
              label={`Condition ${n} time without data`}
              value={condition.window}
              disabled={disabled}
              onChange={(window) => set({ window })}
            />
          </div>
        </>
      ) : (
        <>
          {condition.kind === "aggregate" && (
            <div className="w-40">
              <select
                aria-label={`Condition ${n} function`}
                className={plain}
                value={condition.fn}
                disabled={disabled}
                onChange={(e) => set({ fn: e.target.value })}
              >
                {FUNCTIONS.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="w-full sm:w-64">
            <MetricPicker
              metrics={metrics}
              label={`Condition ${n} metric`}
              value={condition.field}
              disabled={disabled}
              onChange={(field, metric) =>
                set({
                  field,
                  ...(metric
                    ? {
                        operator: metric.suggested_operator as FormOperator,
                        value: String(metric.suggested_value),
                        max: "",
                      }
                    : {}),
                })
              }
            />
          </div>
          {condition.kind === "aggregate" && (
            <>
              <span className="text-sm">over</span>
              <div className="w-36">
                <DurationSelect
                  label={`Condition ${n} window`}
                  value={condition.window}
                  disabled={disabled}
                  onChange={(window) => set({ window })}
                />
              </div>
            </>
          )}
          <div className="w-40">
            <select
              aria-label={`Condition ${n} operator`}
              className={plain}
              value={condition.operator}
              disabled={disabled}
              onChange={(e) => set({ operator: e.target.value as FormOperator })}
            >
              {OPERATORS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className="w-36">
            <Input
              aria-label={`Condition ${n} value`}
              type="number"
              step="any"
              value={condition.value}
              disabled={disabled}
              onChange={(e) => set({ value: e.target.value })}
            />
          </div>
          {condition.operator === "between" && (
            <>
              <span className="text-sm">and</span>
              <div className="w-28">
                <Input
                  aria-label={`Condition ${n} upper limit`}
                  type="number"
                  step="any"
                  value={condition.max}
                  disabled={disabled}
                  onChange={(e) => set({ max: e.target.value })}
                />
              </div>
            </>
          )}
          {unit && <span className="text-sm text-muted-foreground">{unit}</span>}
        </>
      )}
      {onRemove && !disabled && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="ml-auto"
          aria-label={`Remove condition ${n}`}
          onClick={onRemove}
        >
          Remove
        </Button>
      )}
    </div>
  );
}
