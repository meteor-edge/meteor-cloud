import { useId } from "react";

import { cn } from "@/lib/utils";

export type TabItem<T extends string> = {
  id: T;
  label: string;
  count?: number;
};

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: TabItem<T>[];
  active: T;
  onChange: (id: T) => void;
  label: string;
}) {
  const selectId = useId();

  return (
    <>
      <div className="sm:hidden">
        <label htmlFor={selectId} className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </label>
        <select
          id={selectId}
          value={active}
          onChange={(event) => onChange(event.target.value as T)}
          className="h-11 w-full rounded-lg border border-input bg-field px-3 text-sm font-semibold text-foreground"
        >
          {tabs.map((tab) => (
            <option key={tab.id} value={tab.id}>
              {tab.label}{tab.count !== undefined ? ` (${tab.count})` : ""}
            </option>
          ))}
        </select>
        <p className="mt-1.5 text-xs text-muted-foreground">
          Choose a section to view more options.
        </p>
      </div>

      <div role="tablist" aria-label={label} className="tab-list hidden sm:flex">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === active}
            onClick={() => onChange(tab.id)}
            className={cn(
              "tab-trigger",
              tab.id === active && "tab-trigger-active",
            )}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="ml-2 rounded-full bg-background px-1.5 text-xs">{tab.count}</span>
            )}
          </button>
        ))}
      </div>
    </>
  );
}
