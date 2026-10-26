import { NavLink, Outlet } from "react-router-dom";

import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";
import { resolveDocsBaseUrl } from "@/lib/docsUrl";
import { cn } from "@/lib/utils";

export function AppLayout() {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <Header />
        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 sm:py-8">
          <Outlet />
        </main>
        <footer className="border-t border-border px-4 py-4 text-sm text-muted-foreground sm:px-6">
          <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center sm:gap-4">
            <span>MeteorCloud · operator console</span>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <a
                href={resolveDocsBaseUrl()}
                target="_blank"
                rel="noreferrer"
                className="text-link hover:underline"
              >
                Documentation
              </a>
              <NavLink
                to="/health"
                className={({ isActive }) =>
                  cn("text-link hover:underline", isActive && "text-foreground")
                }
              >
                System health
              </NavLink>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
