import { Link } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui/button";
import { useOrganizationContext } from "@/context/OrganizationContext";

export function Header() {
  const { user, isAuthenticated, logout } = useAuth();
  const { selectedOrganization } = useOrganizationContext();

  return (
    <header className="border-b border-border/70 bg-white/60 px-6 py-4 backdrop-blur">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <BrandMark className="md:hidden" />
          <div>
            <p className="text-sm text-muted-foreground">Self-hosted Linux edge control plane</p>
            <h2 className="text-xl font-semibold tracking-tight text-foreground">
              {isAuthenticated ? "Organizations" : "Foundation"}
            </h2>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {isAuthenticated && selectedOrganization && (
            <span
              className="inline-flex items-center gap-2 rounded-md border border-input bg-white px-3 py-1.5 text-sm font-medium text-foreground"
              aria-label="Selected organization"
            >
              <span className="text-xs uppercase tracking-wide text-muted-foreground">Org</span>
              {selectedOrganization.name}
            </span>
          )}
          {isAuthenticated ? (
            <>
              <span className="text-sm text-muted-foreground">{user?.email}</span>
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
