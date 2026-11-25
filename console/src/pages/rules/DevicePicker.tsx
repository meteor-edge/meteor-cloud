import { useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";

import { getDevice, listDevices } from "@/api/fleet/devices";
import { useAuth } from "@/auth/AuthContext";
import { Input } from "@/components/ui/input";

import { useDebounced } from "./shared";

/** Search devices by name and pick one or several. */
export function DevicePicker({
  organizationId,
  value,
  onChange,
  label,
  single,
  disabled,
}: {
  organizationId: string;
  value: string[];
  onChange: (ids: string[]) => void;
  label: string;
  single?: boolean;
  disabled?: boolean;
}) {
  const { token } = useAuth();
  const [search, setSearch] = useState("");
  const term = useDebounced(search, 300);
  const page = useQuery({
    queryKey: ["device-picker", organizationId, token, term],
    queryFn: () =>
      listDevices(token!, organizationId, {
        search: term || undefined,
        page_size: 20,
        sort: "name",
        order: "asc",
      }),
    enabled: Boolean(token && organizationId && !disabled),
  });
  const chosen = useQueries({
    queries: value.slice(0, 20).map((id) => ({
      queryKey: ["device-picker-name", organizationId, token, id],
      queryFn: () => getDevice(token!, organizationId, id),
      enabled: Boolean(token),
      retry: false,
      staleTime: 60_000,
    })),
  });
  const rest = (page.data?.items ?? []).filter((d) => !value.includes(d.id));

  return (
    <div className="space-y-2">
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label={`${label}: chosen`}>
          {value.map((id, i) => (
            <li
              key={id}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary px-2 py-0.5 text-xs"
            >
              {chosen[i]?.data?.name ?? id.slice(0, 8)}
              {!disabled && (
                <button
                  type="button"
                  aria-label={`Remove ${chosen[i]?.data?.name ?? id.slice(0, 8)}`}
                  onClick={() => onChange(value.filter((v) => v !== id))}
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!disabled && !(single && value.length > 0) && (
        <>
          <Input
            aria-label={label}
            placeholder={single ? "Any device, or search…" : "Search devices…"}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {(!single || search !== "") && (
            <ul className="max-h-40 overflow-y-auto rounded-md border border-border text-sm">
              {rest.map((device) => (
                <li key={device.id}>
                  <button
                    type="button"
                    className="w-full px-3 py-1.5 text-left hover:bg-secondary"
                    onClick={() => {
                      onChange(single ? [device.id] : [...value, device.id]);
                      setSearch("");
                    }}
                  >
                    {device.name}
                  </button>
                </li>
              ))}
              {page.data && rest.length === 0 && (
                <li className="px-3 py-1.5 text-muted-foreground">No other device matches.</li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

export function DeviceName({ organizationId, id }: { organizationId: string; id: string }) {
  const { token } = useAuth();
  const device = useQuery({
    queryKey: ["device-picker-name", organizationId, token, id],
    queryFn: () => getDevice(token!, organizationId, id),
    enabled: Boolean(token),
    retry: false,
    staleTime: 60_000,
  });
  return <>{device.data?.name ?? `device ${id.slice(0, 8)}`}</>;
}
