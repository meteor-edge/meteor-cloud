import { Link } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { useOrganizationContext } from "@/context/OrganizationContext";
import { resolveDocsBaseUrl } from "@/lib/docsUrl";

export function Header() {
  const { user, isAuthenticated, logout } = useAuth();
  const { selectedOrganization } = useOrganizationContext();

  return (
    <header className="border-b border-border bg-section px-4 py-3 backdrop-blur sm:px-6 sm:py-4">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <BrandMark className="sm:hidden" showName={false} />
          <BrandMark className="hidden sm:inline-flex md:hidden" />
          <div className="min-w-0">
            <p className="truncate text-xs text-nav sm:text-sm">Self-hosted Linux edge control plane</p>
            <h2 className="text-xl font-semibold tracking-tight text-foreground">
              {isAuthenticated ? "Organizations" : "Foundation"}
            </h2>
          </div>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:gap-3">
          <ThemeToggle />
          <Button variant="secondary" size="sm" asChild>
            <a href={resolveDocsBaseUrl()} target="_blank" rel="noreferrer">
              Docs
            </a>
          </Button>
          {isAuthenticated && selectedOrganization && (
            <span
              className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-md border border-input bg-field px-3 py-1.5 text-sm font-medium text-foreground"
              aria-label="Selected organization"
            >
              <span className="shrink-0 text-xs uppercase tracking-wide text-muted-foreground">Org</span>
              <span className="truncate">{selectedOrganization.name}</span>
            </span>
          )}
          {isAuthenticated ? (
            <>
              <span className="hidden text-sm text-nav lg:inline">{user?.email}</span>
              <Button variant="secondary" size="sm" onClick={logout}>
                Log out
              </Button>
            </>
          ) : (
            <>
              <Button variant="secondary" size="sm" asChild>
                <Link to="/login">Sign in</Link>
              </Button>
              <Button size="sm" asChild>
                <Link to="/register">Register</Link>
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
