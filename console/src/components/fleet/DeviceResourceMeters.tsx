import { cn } from "@/lib/utils";

type Severity = "ok" | "warn" | "critical";

function metricNumber(metrics: Record<string, unknown> | null, key: string): number | null {
  if (!metrics || metrics[key] == null) {
    return null;
  }
  const value = metrics[key];
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function percentSeverity(percent: number): Severity {
  if (percent >= 90) {
    return "critical";
  }
  if (percent >= 75) {
    return "warn";
  }
  return "ok";
}

function temperatureSeverity(celsius: number): Severity {
  if (celsius >= 80) {
    return "critical";
  }
  if (celsius >= 65) {
    return "warn";
  }
  return "ok";
}

const severityStyles: Record<
  Severity,
  { bar: string; glow: string; value: string; chip: string; label: string }
> = {
  ok: {
    bar: "bg-gradient-to-r from-aurora to-meteor-cyan",
    glow: "shadow-[0_0_18px_color-mix(in_srgb,var(--aurora)_35%,transparent)]",
    value: "text-aurora",
    chip: "bg-aurora/15 text-aurora ring-aurora/30",
    label: "Healthy",
  },
  warn: {
    bar: "bg-gradient-to-r from-ember to-[#ffd27a]",
    glow: "shadow-[0_0_18px_color-mix(in_srgb,var(--ember)_40%,transparent)]",
    value: "text-ember",
    chip: "bg-ember/15 text-ember ring-ember/35",
    label: "Elevated",
  },
  critical: {
    bar: "bg-gradient-to-r from-flare to-ember",
    glow: "shadow-[0_0_20px_color-mix(in_srgb,var(--flare)_45%,transparent)]",
    value: "text-flare",
    chip: "bg-flare/15 text-flare ring-flare/40",
    label: "Critical",
  },
};

function Meter({
  label,
  value,
  display,
  fillPercent,
  severity,
}: {
  label: string;
  value: number;
  display: string;
  fillPercent: number;
  severity: Severity;
}) {
  const width = Math.max(0, Math.min(100, fillPercent));
  const tone = severityStyles[severity];

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border border-border bg-field/80 p-4",
        severity === "critical" && "border-flare/40",
        severity === "warn" && "border-ember/35",
      )}
    >
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-20 opacity-70",
          severity === "ok" &&
            "bg-[radial-gradient(ellipse_at_top,color-mix(in_srgb,var(--meteor-cyan)_18%,transparent),transparent_70%)]",
          severity === "warn" &&
            "bg-[radial-gradient(ellipse_at_top,color-mix(in_srgb,var(--ember)_20%,transparent),transparent_70%)]",
          severity === "critical" &&
            "bg-[radial-gradient(ellipse_at_top,color-mix(in_srgb,var(--flare)_22%,transparent),transparent_70%)]",
        )}
      />

      <div className="relative flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            {label}
          </p>
          <p className={cn("mt-1 text-2xl font-semibold tabular-nums tracking-tight", tone.value)}>
            {display}
          </p>
        </div>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset",
            tone.chip,
          )}
        >
          {tone.label}
        </span>
      </div>

      <div
        className="relative mt-4 h-2.5 overflow-hidden rounded-full bg-horizon/55"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(width)}
        aria-valuetext={`${display} (${tone.label})`}
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-500 ease-out",
            tone.bar,
            width > 0 && tone.glow,
          )}
          style={{ width: `${width}%` }}
        />
      </div>

      <div className="relative mt-2 flex justify-between text-[10px] tabular-nums text-muted-foreground/80">
        <span>0</span>
        <span>50</span>
        <span>100</span>
      </div>

      <span className="sr-only">
        {label}: {value}
      </span>
    </div>
  );
}

/** Live CPU / memory / disk / temperature gauges when mqtt_metrics has values. */
export function DeviceResourceMeters({
  metrics,
  updatedAt,
}: {
  metrics: Record<string, unknown> | null;
  updatedAt?: string | null;
}) {
  const cpu = metricNumber(metrics, "cpu_percent");
  const memory = metricNumber(metrics, "memory_percent");
  const disk = metricNumber(metrics, "disk_percent");
  const temperature = metricNumber(metrics, "temperature_c");

  if (cpu == null && memory == null && disk == null && temperature == null) {
    return null;
  }

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-4 shadow-glow sm:p-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-meteor-cyan">
            Telemetry
          </p>
          <h2 className="mt-1 text-base font-semibold text-foreground">Live resources</h2>
        </div>
        {updatedAt ? (
          <p className="text-xs text-muted-foreground">Updated {updatedAt}</p>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cpu != null ? (
          <Meter
            label="CPU"
            value={cpu}
            display={`${cpu.toFixed(1)}%`}
            fillPercent={cpu}
            severity={percentSeverity(cpu)}
          />
        ) : null}
        {memory != null ? (
          <Meter
            label="Memory"
            value={memory}
            display={`${memory.toFixed(1)}%`}
            fillPercent={memory}
            severity={percentSeverity(memory)}
          />
        ) : null}
        {disk != null ? (
          <Meter
            label="Disk"
            value={disk}
            display={`${disk.toFixed(1)}%`}
            fillPercent={disk}
            severity={percentSeverity(disk)}
          />
        ) : null}
        {temperature != null ? (
          <Meter
            label="Temperature"
            value={temperature}
            display={`${temperature.toFixed(1)} °C`}
            fillPercent={temperature}
            severity={temperatureSeverity(temperature)}
          />
        ) : null}
      </div>
    </div>
  );
}
