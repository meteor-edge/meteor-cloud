import { useEffect, useId, useRef, useState } from "react";

import type { Metric } from "@/api/rules";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { formatNumber } from "./shared";

const FIELD = /^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/;

function range(metric: Metric): string {
  if (metric.devices === 0) return "not reported yet";
  const devices = `${metric.devices} device${metric.devices === 1 ? "" : "s"}`;
  if (metric.min === null || metric.max === null) return devices;
  const low = formatNumber(metric.min, metric.unit);
  const high = formatNumber(metric.max, metric.unit);
  return `${devices} · now ${low === high ? low : `${low} – ${high}`}`;
}

/** Search box over every metric rules can use; any field name can also be typed. */
export function MetricPicker({
  metrics,
  value,
  onChange,
  label,
  disabled,
}: {
  metrics: Metric[];
  value: string;
  onChange: (field: string, metric?: Metric) => void;
  label: string;
  disabled?: boolean;
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [cursor, setCursor] = useState(0);
  const current = metrics.find((m) => m.field === value);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const found = metrics
    .filter((m) => {
      const text = `${m.field} ${m.label} ${m.group} ${m.description}`.toLowerCase();
      return words.every((w) => text.includes(w));
    })
    // Metrics devices report come first.
    .sort((a, b) => Number(b.devices > 0) - Number(a.devices > 0))
    .slice(0, 100);
  const custom = search.trim();
  const offerCustom = FIELD.test(custom) && !metrics.some((m) => m.field === custom);

  const choose = (field: string, metric?: Metric) => {
    onChange(field, metric);
    setOpen(false);
    setSearch("");
  };

  return (
    <div className="relative" ref={root}>
      <Input
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={open && found[cursor] ? `${id}-${cursor}` : undefined}
        disabled={disabled}
        placeholder="Search metrics…"
        value={open ? search : (current?.label ?? value)}
        onFocus={() => {
          setOpen(true);
          setSearch("");
          setCursor(0);
        }}
        onBlur={(event) => {
          if (!root.current?.contains(event.relatedTarget as Node)) setOpen(false);
        }}
        onChange={(event) => {
          setSearch(event.target.value);
          setOpen(true);
          setCursor(0);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
            setCursor((c) => Math.min(c + 1, found.length - 1));
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            setCursor((c) => Math.max(c - 1, 0));
          }
          if (event.key === "Enter" && open) {
            event.preventDefault();
            if (found[cursor]) choose(found[cursor].field, found[cursor]);
            else if (offerCustom) choose(custom);
          }
        }}
      />
      {open && (
        <ul
          id={`${id}-list`}
          role="listbox"
          aria-label={`${label} options`}
          className="absolute z-30 mt-1 max-h-72 w-full min-w-72 overflow-y-auto rounded-md border border-border bg-background shadow-lg"
        >
          {found.map((metric, index) => (
            <li
              key={metric.field}
              id={`${id}-${index}`}
              role="option"
              aria-selected={metric.field === value}
              className={cn(
                "cursor-pointer px-3 py-2 text-sm",
                index === cursor ? "bg-secondary" : "hover:bg-secondary",
                metric.devices === 0 && "text-muted-foreground",
              )}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(metric.field, metric)}
            >
              <div className="flex justify-between gap-2">
                <span className="font-medium">{metric.label}</span>
                <span className="text-xs text-muted-foreground">{metric.group}</span>
              </div>
              <div className="flex flex-wrap justify-between gap-x-2 text-xs text-muted-foreground">
                <code>{metric.field}</code>
                <span>{range(metric)}</span>
              </div>
            </li>
          ))}
          {offerCustom && (
            <li
              role="option"
              aria-selected={false}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-secondary"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(custom)}
            >
              Use field <code>{custom}</code>
            </li>
          )}
          {found.length === 0 && !offerCustom && (
            <li className="px-3 py-2 text-sm text-muted-foreground">No metric matches.</li>
          )}
        </ul>
      )}
    </div>
  );
}
