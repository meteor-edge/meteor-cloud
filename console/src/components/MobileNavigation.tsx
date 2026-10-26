import { Menu, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link, NavLink, useLocation } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui/button";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { resolveDocsBaseUrl } from "@/lib/docsUrl";
import { canManageMembers, canReadTeams } from "@/lib/permissions";
import { cn } from "@/lib/utils";

function sectionClass(active: boolean) {
  return cn(
    "block rounded-md px-3 py-2 text-sm font-medium text-nav transition hover:bg-secondary hover:text-foreground",
    active && "bg-accent text-accent-foreground",
  );
}

function MobileSubnav({ children }: { children: ReactNode }) {
  return <div className="ml-2 mt-1 space-y-1 border-l border-border pl-3">{children}</div>;
}

export function MobileNavigation() {
  const [open, setOpen] = useState(false);
  const { isAuthenticated } = useAuth();
  const { selectedOrganization } = useOrganizationContext();
  const { pathname, search } = useLocation();
  const organizationId = selectedOrganization?.id;
  const organizationPath = `/organizations/${organizationId}`;
  const isPage = (page: string) => pathname.startsWith(`${organizationPath}/${page}`);
  const artifactType = new URLSearchParams(search).get("type");
  const role = selectedOrganization?.current_user_role;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="w-9 shrink-0 px-0 md:hidden"
        aria-label="Open navigation menu"
        onClick={() => setOpen(true)}
      >
        <Menu className="h-4 w-4" />
      </Button>

      {open && createPortal(
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation menu">
          <button
            type="button"
            className="absolute inset-0 cursor-default bg-overlay"
            aria-label="Close navigation menu"
            onClick={() => setOpen(false)}
          />
          <div className="relative flex h-full w-[min(20rem,calc(100vw-3rem))] flex-col border-r border-border bg-section shadow-2xl backdrop-blur">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <BrandMark />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-9 px-0"
                aria-label="Close navigation menu"
                onClick={() => setOpen(false)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>

            <nav aria-label="Mobile navigation" className="flex-1 overflow-y-auto p-4">
              {!organizationId && (
                <NavLink to="/" end onClick={() => setOpen(false)} className={({ isActive }) => sectionClass(isActive)}>
                  Home
                </NavLink>
              )}

              {isAuthenticated && (
                <>
                  <NavLink to="/organizations" end onClick={() => setOpen(false)} className={({ isActive }) => sectionClass(isActive)}>
                    Organizations
                  </NavLink>

                  {organizationId && (
                    <div className="mt-5 space-y-1">
                      <p className="truncate px-3 pb-1 text-xs font-semibold uppercase tracking-[0.14em] text-nav">
                        {selectedOrganization?.name}
                      </p>
                      <NavLink to={organizationPath} end onClick={() => setOpen(false)} className={({ isActive }) => sectionClass(isActive)}>
                        Overview
                      </NavLink>
                      <p className="px-3 pt-3 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Devices</p>
                      <MobileSubnav>
                        <NavLink to={`${organizationPath}/devices`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("devices"))}>
                          All devices
                        </NavLink>
                        <NavLink to={`${organizationPath}/device-groups`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("device-groups"))}>
                          Device groups
                        </NavLink>
                        <NavLink to={`${organizationPath}/device-types`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("device-types"))}>
                          Device types
                        </NavLink>
                      </MobileSubnav>
                      <p className="px-3 pt-3 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Platform</p>
                      <MobileSubnav>
                        <NavLink to={`${organizationPath}/artifacts`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("artifacts") && !artifactType)}>
                          Artifacts
                        </NavLink>
                        <NavLink to={`${organizationPath}/artifacts?type=os_image`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("artifacts") && artifactType === "os_image")}>
                          OS images
                        </NavLink>
                        <NavLink to={`${organizationPath}/releases`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("releases"))}>
                          Releases
                        </NavLink>
                        <NavLink to={`${organizationPath}/deployments`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("deployments"))}>
                          Deployments
                        </NavLink>
                        <NavLink to={`${organizationPath}/endpoints`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("endpoints"))}>
                          Endpoints
                        </NavLink>
                        <NavLink to={`${organizationPath}/mqtt`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("mqtt"))}>
                          Monitoring
                        </NavLink>
                      </MobileSubnav>
                      {(canManageMembers(role) || canReadTeams(role)) && (
                        <>
                          <p className="px-3 pt-3 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">People</p>
                          <MobileSubnav>
                            {canManageMembers(role) && (
                              <NavLink to={`${organizationPath}/members`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("members"))}>
                                Members
                              </NavLink>
                            )}
                            {canReadTeams(role) && (
                              <NavLink to={`${organizationPath}/teams`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("teams"))}>
                                Teams
                              </NavLink>
                            )}
                          </MobileSubnav>
                        </>
                      )}
                      <NavLink to={`${organizationPath}/settings`} onClick={() => setOpen(false)} className={() => sectionClass(isPage("settings") || isPage("api-keys"))}>
                        Settings
                      </NavLink>
                    </div>
                  )}
                </>
              )}

              {!isAuthenticated && (
                <div className="mt-2 space-y-1">
                  <Link to="/login" onClick={() => setOpen(false)} className={sectionClass(pathname === "/login")}>Sign in</Link>
                  <Link to="/register" onClick={() => setOpen(false)} className={sectionClass(pathname === "/register")}>Register</Link>
                </div>
              )}
            </nav>
            <div className="border-t border-border p-4">
              <a href={resolveDocsBaseUrl()} target="_blank" rel="noreferrer" className="block rounded-md px-3 py-2 text-sm font-medium text-link hover:bg-secondary" onClick={() => setOpen(false)}>
                Documentation
              </a>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
