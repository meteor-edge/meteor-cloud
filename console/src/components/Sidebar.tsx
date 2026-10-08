import {
  Activity,
  BookOpen,
  Boxes,
  Building2,
  ChevronDown,
  CircuitBoard,
  HardDrive,
  House,
  Layers,
  LayoutDashboard,
  List,
  LogIn,
  Package,
  Router,
  Settings,
  UserPlus,
  UserRound,
  Users,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { resolveDocsBaseUrl } from "@/lib/docsUrl";
import { canManageMembers, canReadTeams } from "@/lib/permissions";
import { cn } from "@/lib/utils";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground",
    isActive && "bg-accent text-accent-foreground",
  );

const subLinkClass = (isActive: boolean) =>
  cn(
    "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground",
    isActive && "bg-accent text-accent-foreground",
  );

// Enrollment API keys are reached from the Settings page.
function isSettingsSection(pathname: string, orgId: string): boolean {
  return pathname.startsWith(`/organizations/${orgId}/api-keys`);
}

/** A collapsible menu group. The heading names the area; the pages live underneath. */
function NavSection({
  icon: Icon,
  label,
  active,
  children,
}: {
  icon: LucideIcon;
  label: string;
  active: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground",
          active && "text-foreground",
        )}
      >
        <Icon className="h-4 w-4" />
        <span className="flex-1 text-left">{label}</span>
        <ChevronDown
          className={cn("h-4 w-4 transition-transform", !open && "-rotate-90")}
          aria-hidden
        />
      </button>
      {open && (
        <div className="ml-5 mt-0.5 flex flex-col gap-0.5 border-l border-border pl-2">
          {children}
        </div>
      )}
    </div>
  );
}

// Activity is computed by the caller because OS Images and All Artifacts share a path
// and differ only by query string, which NavLink ignores.
function SubLink({
  to,
  icon: Icon,
  active,
  children,
}: {
  to: string;
  icon: LucideIcon;
  active: boolean;
  children: ReactNode;
}) {
  return (
    <Link to={to} className={subLinkClass(active)} aria-current={active ? "page" : undefined}>
      <Icon className="h-3.5 w-3.5" />
      {children}
    </Link>
  );
}

export function Sidebar() {
  const { isAuthenticated } = useAuth();
  const { selectedOrganization } = useOrganizationContext();
  const { pathname, search } = useLocation();

  const orgId = selectedOrganization?.id;
  const orgPath = `/organizations/${orgId}`;
  const isPage = (page: string) => pathname.startsWith(`${orgPath}/${page}`);
  const artifactType = new URLSearchParams(search).get("type");
  const role = selectedOrganization?.current_user_role;
  const showMembers = canManageMembers(role);
  const showTeams = canReadTeams(role);

  return (
    <aside className="hidden w-64 shrink-0 border-r border-border bg-section backdrop-blur md:flex md:flex-col">
      <div className="border-b border-border px-6 py-5">
        <BrandMark iconClassName="h-9 w-9" />
        <p className="mt-3 text-xs font-semibold uppercase tracking-[0.18em] text-link">
          Edge · Data · OTA
        </p>
        <h1 className="mt-1 text-sm font-medium text-nav">Control plane</h1>
      </div>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-1 p-4">
        {!orgId && (
          <NavLink to="/" end className={linkClass}>
            <House className="h-4 w-4" />
            Home
          </NavLink>
        )}

        {isAuthenticated && (
          <>
            <NavLink to="/organizations" end className={linkClass}>
              <Building2 className="h-4 w-4" />
              Organizations
            </NavLink>

            {/* Once an organization is selected the menu is scoped to it. */}
            {orgId && (
              <div className="mt-4 flex flex-col gap-1">
                <p className="truncate px-3 pb-1 text-xs font-semibold uppercase tracking-[0.14em] text-nav">
                  {selectedOrganization?.name}
                </p>
                <NavLink to={`/organizations/${orgId}`} end className={linkClass}>
                  <LayoutDashboard className="h-4 w-4" />
                  Overview
                </NavLink>

                <NavSection
                  icon={Router}
                  label="Devices"
                  active={["devices", "device-groups", "device-types"].some(isPage)}
                >
                  <SubLink to={`${orgPath}/devices`} icon={List} active={isPage("devices")}>
                    All Devices
                  </SubLink>
                  <SubLink
                    to={`${orgPath}/device-groups`}
                    icon={Layers}
                    active={isPage("device-groups")}
                  >
                    Groups
                  </SubLink>
                  <SubLink
                    to={`${orgPath}/device-types`}
                    icon={CircuitBoard}
                    active={isPage("device-types")}
                  >
                    Types
                  </SubLink>
                </NavSection>

                <NavSection icon={Package} label="Artifacts" active={isPage("artifacts")}>
                  <SubLink
                    to={`${orgPath}/artifacts`}
                    icon={Boxes}
                    active={isPage("artifacts") && !artifactType}
                  >
                    All Artifacts
                  </SubLink>
                  <SubLink
                    to={`${orgPath}/artifacts?type=os_image`}
                    icon={HardDrive}
                    active={isPage("artifacts") && artifactType === "os_image"}
                  >
                    OS Images
                  </SubLink>
                </NavSection>

                <NavLink to={`/organizations/${orgId}/mqtt`} className={linkClass}>
                  <Activity className="h-4 w-4" />
                  Monitoring
                </NavLink>
                {(showMembers || showTeams) && (
                  <NavSection
                    icon={Users}
                    label="People"
                    active={["members", "teams"].some(isPage)}
                  >
                    {showMembers && (
                      <SubLink to={`${orgPath}/members`} icon={UserRound} active={isPage("members")}>
                        Members
                      </SubLink>
                    )}
                    {showTeams && (
                      <SubLink to={`${orgPath}/teams`} icon={UsersRound} active={isPage("teams")}>
                        Teams
                      </SubLink>
                    )}
                  </NavSection>
                )}
                <NavLink
                  to={`/organizations/${orgId}/settings`}
                  className={({ isActive }) =>
                    linkClass({ isActive: isActive || isSettingsSection(pathname, orgId) })
                  }
                >
                  <Settings className="h-4 w-4" />
                  Settings
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
      <div className="border-t border-border p-4">
        <a
          href={resolveDocsBaseUrl()}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground"
        >
          <BookOpen className="h-4 w-4" />
          Documentation
        </a>
      </div>
    </aside>
  );
}
