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
    <nav
      aria-label="Fleet sections"
      className="grid w-full grid-cols-1 gap-1 rounded-lg border border-border bg-card p-1.5 shadow-glow sm:grid-cols-3"
    >
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          className={({ isActive }) =>
            cn(
              "fleet-tab",
              isActive && "fleet-tab-active",
            )
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  );
}
