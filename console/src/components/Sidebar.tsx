import {
  BookOpen,
  Boxes,
  Building2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircuitBoard,
  Globe,
  HardDrive,
  House,
  Layers,
  LayoutDashboard,
  List,
  LogIn,
  Package,
  Rocket,
  Router,
  Send,
  Settings,
  Tag,
  UserPlus,
  UserRound,
  Users,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { resolveDocsBaseUrl } from "@/lib/docsUrl";
import { canManageMembers, canReadTeams } from "@/lib/permissions";
import { cn } from "@/lib/utils";

const SIDEBAR_COLLAPSED_KEY = "meteorcloud-sidebar-collapsed";

const linkClass = ({ isActive }: { isActive: boolean }, collapsed: boolean) =>
  cn(
    "flex h-10 min-w-0 items-center overflow-hidden whitespace-nowrap rounded-md text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground",
    collapsed ? "justify-center px-0" : "gap-3 px-3",
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
  collapsed = false,
  onExpand = () => {},
  children,
}: {
  icon: LucideIcon;
  label: string;
  active: boolean;
  collapsed?: boolean;
  onExpand?: () => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-label={collapsed ? label : undefined}
        title={collapsed ? label : undefined}
        onClick={() => {
          if (collapsed) {
            onExpand();
            setOpen(true);
            return;
          }
          setOpen((value) => !value);
        }}
        className={cn(
          "flex h-10 w-full min-w-0 items-center rounded-md text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground",
          collapsed ? "justify-center px-0" : "gap-3 px-3",
          active && "text-foreground",
        )}
      >
        <Icon className="h-4 w-4" />
        {!collapsed && (
          <>
            <span className="flex-1 text-left">{label}</span>
            <ChevronDown
              className={cn("h-4 w-4 transition-transform", !open && "-rotate-90")}
              aria-hidden
            />
          </>
        )}
      </button>
      {open && !collapsed && (
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
  collapsed = false,
  children,
}: {
  to: string;
  icon: LucideIcon;
  active: boolean;
  collapsed?: boolean;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      className={subLinkClass(active)}
      aria-current={active ? "page" : undefined}
      title={collapsed ? String(children) : undefined}
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </Link>
  );
}

export function Sidebar() {
  const { isAuthenticated } = useAuth();
  const { selectedOrganization } = useOrganizationContext();
  const { pathname, search } = useLocation();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true",
  );

  useEffect(() => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
  }, [collapsed]);

  const orgId = selectedOrganization?.id;
  const orgPath = `/organizations/${orgId}`;
  const isPage = (page: string) => pathname.startsWith(`${orgPath}/${page}`);
  const artifactType = new URLSearchParams(search).get("type");
  const role = selectedOrganization?.current_user_role;
  const showMembers = canManageMembers(role);
  const showTeams = canReadTeams(role);

  return (
    <aside
      className={cn(
        "relative z-20 hidden shrink-0 border-r border-border bg-section transition-[width] duration-200 backdrop-blur md:flex md:flex-col",
        collapsed ? "w-20" : "w-64",
      )}
    >
      <div className={cn("border-b border-border py-5", collapsed ? "px-4" : "px-6")}>
        <BrandMark iconClassName="h-9 w-9" showName={!collapsed} />
        <p className={cn("mt-3 text-xs font-semibold uppercase tracking-[0.18em] text-link", collapsed && "hidden")}>
          Edge · Data · OTA
        </p>
        <h1 className={cn("mt-1 text-sm font-medium text-nav", collapsed && "hidden")}>Control plane</h1>
      </div>
      <button
        type="button"
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        onClick={() => setCollapsed((value) => !value)}
        className="absolute -right-3 top-5 z-10 inline-flex h-6 w-6 items-center justify-center rounded-full border border-border bg-card text-nav shadow-sm transition hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
      </button>
      <nav aria-label="Main" className={cn("flex flex-1 flex-col gap-1", collapsed ? "p-3" : "p-4")}>
        {!orgId && (
          <NavLink to="/" end className={(state) => linkClass(state, collapsed)} title="Home">
            <House className="h-4 w-4" />
            <span className={collapsed ? "sr-only" : undefined}>Home</span>
          </NavLink>
        )}

        {isAuthenticated && (
          <>
            <NavLink
              to="/organizations"
              end
              className={(state) => linkClass(state, collapsed)}
              title="Organizations"
            >
              <Building2 className="h-4 w-4" />
              <span className={collapsed ? "sr-only" : undefined}>Organizations</span>
            </NavLink>

            {/* Once an organization is selected the menu is scoped to it. */}
            {orgId && (
              <div className={cn("flex flex-col gap-1", collapsed ? "mt-2" : "mt-4")}>
                <p
                  className={cn(
                    "truncate px-3 pb-1 text-xs font-semibold uppercase tracking-[0.14em] text-nav",
                    collapsed && "hidden",
                  )}
                >
                  {selectedOrganization?.name}
                </p>
                <NavLink
                  to={`/organizations/${orgId}`}
                  end
                  className={(state) => linkClass(state, collapsed)}
                  title="Overview"
                >
                  <LayoutDashboard className="h-4 w-4" />
                  <span className={collapsed ? "sr-only" : undefined}>Overview</span>
                </NavLink>

                <NavSection
                  icon={Router}
                  label="Devices"
                  active={["devices", "device-groups", "device-types"].some(isPage)}
                  collapsed={collapsed}
                  onExpand={() => setCollapsed(false)}
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

                <NavSection
                  icon={Package}
                  label="Artifacts"
                  active={isPage("artifacts")}
                  collapsed={collapsed}
                  onExpand={() => setCollapsed(false)}
                >
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

                <NavSection
                  icon={Rocket}
                  label="Software"
                  active={["releases", "deployments", "endpoints"].some(isPage)}
                  collapsed={collapsed}
                  onExpand={() => setCollapsed(false)}
                >
                  <SubLink to={`${orgPath}/releases`} icon={Tag} active={isPage("releases")}>
                    Releases
                  </SubLink>
                  <SubLink
                    to={`${orgPath}/deployments`}
                    icon={Send}
                    active={isPage("deployments")}
                  >
                    Deployments
                  </SubLink>
                  <SubLink
                    to={`${orgPath}/endpoints`}
                    icon={Globe}
                    active={isPage("endpoints")}
                  >
                    Endpoints
                  </SubLink>
                </NavSection>
                {(showMembers || showTeams) && (
                  <NavSection
                    icon={Users}
                    label="People"
                    active={["members", "teams"].some(isPage)}
                    collapsed={collapsed}
                    onExpand={() => setCollapsed(false)}
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
                    linkClass({ isActive: isActive || isSettingsSection(pathname, orgId) }, collapsed)
                  }
                  title="Settings"
                >
                  <Settings className="h-4 w-4" />
                  <span className={collapsed ? "sr-only" : undefined}>Settings</span>
                </NavLink>
              </div>
            )}
          </>
        )}

        {!isAuthenticated && (
          <>
            <NavLink to="/login" className={(state) => linkClass(state, collapsed)} title="Sign in">
              <LogIn className="h-4 w-4" />
              <span className={collapsed ? "sr-only" : undefined}>Sign in</span>
            </NavLink>
            <NavLink to="/register" className={(state) => linkClass(state, collapsed)} title="Register">
              <UserPlus className="h-4 w-4" />
              <span className={collapsed ? "sr-only" : undefined}>Register</span>
            </NavLink>
          </>
        )}
      </nav>
      <div className={cn("border-t border-border", collapsed ? "p-3" : "p-4")}>
        <a
          href={resolveDocsBaseUrl()}
          target="_blank"
          rel="noreferrer"
          className={cn(
            "flex h-10 items-center overflow-hidden whitespace-nowrap rounded-md text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground",
            collapsed ? "justify-center px-0" : "gap-3 px-3",
          )}
          title="Documentation"
        >
          <BookOpen className="h-4 w-4" />
          <span className={collapsed ? "sr-only" : undefined}>Documentation</span>
        </a>
      </div>
    </aside>
  );
}
