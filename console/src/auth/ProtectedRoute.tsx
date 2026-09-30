import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, token } = useAuth();
  const location = useLocation();

  if (token && isLoading) {
    return <p className="p-8 text-muted-foreground">Loading session…</p>;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return children;
}
