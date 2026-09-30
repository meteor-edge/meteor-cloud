import { NavLink, Outlet } from "react-router-dom";

import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";
import { cn } from "@/lib/utils";

export function AppLayout() {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-h-screen flex-1 flex-col">
        <Header />
        <main className="flex-1 px-6 py-8">
          <Outlet />
        </main>
        <footer className="border-t border-border/70 px-6 py-4 text-sm text-muted-foreground">
          <div className="flex items-center justify-between gap-4">
            <span>MeteorEdge · control plane</span>
            <NavLink
              to="/health"
              className={({ isActive }) =>
                cn("hover:text-foreground", isActive && "text-foreground")
              }
            >
              System health
            </NavLink>
          </div>
        </footer>
      </div>
    </div>
  );
}
