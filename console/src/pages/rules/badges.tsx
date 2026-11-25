import type { AlertStatus, Severity } from "@/api/rules";
import { cn } from "@/lib/utils";

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-destructive/40 bg-destructive/10 text-destructive",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  info: "border-border bg-secondary text-muted-foreground",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold",
        SEVERITY_CLASS[severity],
      )}
    >
      {severity}
    </span>
  );
}

export function StatusBadge({ status, silenced }: { status: AlertStatus; silenced?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 text-sm">
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          status === "firing"
            ? "bg-destructive"
            : status === "acknowledged"
              ? "bg-amber-500"
              : "bg-muted-foreground",
        )}
        aria-hidden
      />
      {status}
      {silenced && <span className="text-xs text-muted-foreground">(silenced)</span>}
    </span>
  );
}
