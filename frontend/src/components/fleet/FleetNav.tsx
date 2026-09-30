import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";

export function FleetNav({ organizationId }: { organizationId: string }) {
  const tabs = [
    { to: `/organizations/${organizationId}/device-types`, label: "Device types" },
    { to: `/organizations/${organizationId}/device-groups`, label: "Device groups" },
    { to: `/organizations/${organizationId}/devices`, label: "Devices" },
    { to: `/organizations/${organizationId}/mqtt`, label: "MQTT test" },
    { to: `/organizations/${organizationId}/api-keys`, label: "API keys" },
  ];

  return (
    <nav className="flex flex-wrap gap-2 border-b border-border/70 pb-3">
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          className={({ isActive }) =>
            cn(
              "rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground transition hover:bg-secondary hover:text-foreground",
              isActive && "bg-accent text-accent-foreground",
            )
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  );
}
