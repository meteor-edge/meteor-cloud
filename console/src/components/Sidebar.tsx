import {
  Activity,
  BookOpen,
  Boxes,
  Building2,
  Cpu,
  KeyRound,
  Layers,
  LayoutDashboard,
  LogIn,
  Radio,
  UserPlus,
} from "lucide-react";
import { NavLink } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { resolveDocsBaseUrl } from "@/lib/docsUrl";
import { cn } from "@/lib/utils";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition hover:bg-secondary hover:text-foreground",
    isActive && "bg-accent text-accent-foreground",
  );

export function Sidebar() {
  const { isAuthenticated } = useAuth();
  const { selectedOrganization } = useOrganizationContext();

  const orgId = selectedOrganization?.id;

  return (
    <aside className="hidden w-64 shrink-0 border-r border-border/70 bg-white/70 backdrop-blur md:flex md:flex-col">
      <div className="border-b border-border/70 px-6 py-5">
        <BrandMark iconClassName="h-9 w-9" />
        <p className="mt-3 text-xs font-semibold uppercase tracking-[0.18em] text-primary">
          Edge · Data · OTA
        </p>
        <h1 className="mt-1 text-sm font-medium text-muted-foreground">Control plane</h1>
      </div>
      <nav className="flex flex-1 flex-col gap-1 p-4">
        <NavLink to="/" end className={linkClass}>
          <LayoutDashboard className="h-4 w-4" />
          Overview
        </NavLink>
        <NavLink to="/health" className={linkClass}>
          <Activity className="h-4 w-4" />
          Health
        </NavLink>

        {isAuthenticated && (
          <>
            <NavLink to="/organizations" className={linkClass}>
              <Building2 className="h-4 w-4" />
              Organizations
            </NavLink>

            {/* Once an organization is selected the fleet menu is scoped to it. */}
            {orgId && (
              <div className="mt-4">
                <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Fleet
                </p>
                <NavLink to={`/organizations/${orgId}/device-types`} className={linkClass}>
                  <Boxes className="h-4 w-4" />
                  Device types
                </NavLink>
                <NavLink to={`/organizations/${orgId}/device-groups`} className={linkClass}>
                  <Layers className="h-4 w-4" />
                  Device groups
                </NavLink>
                <NavLink to={`/organizations/${orgId}/devices`} className={linkClass}>
                  <Cpu className="h-4 w-4" />
                  Devices
                </NavLink>
                <NavLink to={`/organizations/${orgId}/mqtt`} className={linkClass}>
                  <Radio className="h-4 w-4" />
                  MQTT test
                </NavLink>
                <NavLink to={`/organizations/${orgId}/api-keys`} className={linkClass}>
                  <KeyRound className="h-4 w-4" />
                  API keys
                </NavLink>
              </div>
            )}
          </>
        )}

        {!isAuthenticated && (
          <>
            <NavLink to="/login" className={linkClass}>
              <LogIn className="h-4 w-4" />
              Sign in
            </NavLink>
            <NavLink to="/register" className={linkClass}>
              <UserPlus className="h-4 w-4" />
              Register
            </NavLink>
          </>
        )}
      </nav>
      <div className="border-t border-border/70 p-4">
        <a
          href={resolveDocsBaseUrl()}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition hover:bg-secondary hover:text-foreground"
        >
          <BookOpen className="h-4 w-4" />
          Documentation
        </a>
      </div>
    </aside>
  );
}
