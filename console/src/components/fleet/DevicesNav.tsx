import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";

/** Tabs shared by the Devices, Device Groups, and Device Types pages. */
export function DevicesNav({ organizationId }: { organizationId: string }) {
  const tabs = [
    { to: `/organizations/${organizationId}/devices`, label: "Devices" },
    { to: `/organizations/${organizationId}/device-groups`, label: "Device Groups" },
    { to: `/organizations/${organizationId}/device-types`, label: "Device Types" },
  ];

  return (
    <nav className="flex flex-wrap gap-2 border-b border-border pb-3">
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
