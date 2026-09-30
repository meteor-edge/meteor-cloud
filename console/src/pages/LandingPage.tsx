import { Link } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";

export function LandingPage() {
  const { isAuthenticated } = useAuth();

  return (
    <section className="mx-auto max-w-3xl">
      <img
        src="/brand/banner.png"
        alt="MeteorEdge — Edge, Data, OTA"
        className="mb-8 w-full rounded-lg shadow-sm"
      />
      <p className="text-sm font-semibold uppercase tracking-[0.2em] text-primary">
        Edge · Data · OTA
      </p>
      <h1 className="mt-3 text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
        Identity and organizations for the control plane
      </h1>
      <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
        Milestone 2 adds user authentication, multi-tenant organizations, and role-based membership
        management on top of the Milestone 1 foundation.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        {isAuthenticated ? (
          <Button asChild>
            <Link to="/organizations">View organizations</Link>
          </Button>
        ) : (
          <>
            <Button asChild>
              <Link to="/register">Get started</Link>
            </Button>
            <Button variant="secondary" asChild>
              <Link to="/login">Sign in</Link>
            </Button>
          </>
        )}
        <Button variant="outline" asChild>
          <Link to="/health">Check health</Link>
        </Button>
      </div>
    </section>
  );
}
