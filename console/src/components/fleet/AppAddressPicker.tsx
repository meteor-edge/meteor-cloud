import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";

import { checkHostname, suggestHostname } from "@/api/fleet";
import { Input } from "@/components/ui/input";

type AppAddressPickerProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  /** Used for the suggested address: <app>.<device>.<org>.<apps domain>. */
  appName: string;
  /** The chosen hostname once it is known to be available, otherwise null. */
  onChange: (hostname: string | null) => void;
};

const CHECK_DELAY_MS = 400;

function useDebounced(value: string, delay: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function Status({ state, message }: { state: "checking" | "ok" | "bad"; message: string }) {
  if (state === "checking") {
    return (
      <span className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking…
      </span>
    );
  }
  return (
    <span
      className={`flex items-center gap-1 text-xs ${state === "ok" ? "text-green-600" : "text-destructive"}`}
    >
      {state === "ok" ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />} {message}
    </span>
  );
}

/** Choose a public address for an app: the platform's suggestion or a custom name, checked as you type. */
export function AppAddressPicker({
  token,
  organizationId,
  deviceId,
  appName,
  onChange,
}: AppAddressPickerProps) {
  const [mode, setMode] = useState<"suggested" | "custom">("suggested");
  const [custom, setCustom] = useState("");
  const app = useDebounced(appName.trim() || "app", CHECK_DELAY_MS);

  const suggestion = useQuery({
    queryKey: ["hostname-suggestion", organizationId, deviceId, app, token],
    queryFn: () => suggestHostname(token, organizationId, deviceId, app),
  });
  const domain = suggestion.data?.apps_domain ?? "";
  const label = useDebounced(custom.trim().toLowerCase(), CHECK_DELAY_MS);
  const customHostname = label && domain ? `${label}.${domain}` : "";
  const check = useQuery({
    queryKey: ["hostname-check", organizationId, deviceId, customHostname, token],
    queryFn: () => checkHostname(token, organizationId, deviceId, customHostname),
    enabled: mode === "custom" && Boolean(customHostname),
  });
  const typing = custom.trim().toLowerCase() !== label;

  const chosen =
    mode === "suggested"
      ? suggestion.data?.available
        ? suggestion.data.hostname
        : null
      : !typing && check.data?.available
        ? check.data.hostname
        : null;
  useEffect(() => onChange(chosen), [chosen, onChange]);

  return (
    <div className="space-y-2">
      <label className="flex items-start gap-2 text-sm">
        <input
          type="radio"
          className="mt-1"
          checked={mode === "suggested"}
          onChange={() => setMode("suggested")}
        />
        <span className="min-w-0 space-y-1">
          <span className="font-medium">Suggested</span>
          <span className="block break-all font-mono text-xs text-muted-foreground">
            {suggestion.data?.hostname ?? "…"}
          </span>
          {mode === "suggested" && suggestion.data && (
            <Status
              state={suggestion.data.available ? "ok" : "bad"}
              message={
                suggestion.data.available
                  ? "Available"
                  : "No free suggestion; choose a custom name."
              }
            />
          )}
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm">
        <input
          type="radio"
          className="mt-1"
          checked={mode === "custom"}
          onChange={() => setMode("custom")}
        />
        <span className="min-w-0 flex-1 space-y-1">
          <span className="font-medium">Custom name</span>
          <span className="flex items-center gap-1">
            <Input
              aria-label="Custom name"
              className="h-8 max-w-[12rem] font-mono text-xs"
              placeholder="shop"
              value={custom}
              onFocus={() => setMode("custom")}
              onChange={(event) => setCustom(event.target.value)}
            />
            <span className="break-all font-mono text-xs text-muted-foreground">
              .{domain || "…"}
            </span>
          </span>
          {mode === "custom" && custom.trim() && (
            <Status
              state={typing || check.isFetching ? "checking" : check.data?.available ? "ok" : "bad"}
              message={check.data?.message ?? ""}
            />
          )}
        </span>
      </label>
    </div>
  );
}
