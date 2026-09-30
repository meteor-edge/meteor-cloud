import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";

import { listOrganizations, type Organization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";

const SELECTED_ORG_KEY = "edge_platform_selected_org";

type OrganizationContextValue = {
  organizations: Organization[];
  selectedOrganization: Organization | null;
  selectOrganization: (organizationId: string) => void;
  isLoading: boolean;
  refreshOrganizations: () => Promise<unknown>;
};

const OrganizationContext = createContext<OrganizationContextValue | null>(null);

export function OrganizationProvider({ children }: { children: ReactNode }) {
  const { token, isAuthenticated } = useAuth();
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    localStorage.getItem(SELECTED_ORG_KEY),
  );

  const orgsQuery = useQuery({
    queryKey: ["organizations", token],
    queryFn: () => listOrganizations(token!),
    enabled: Boolean(token && isAuthenticated),
  });

  const organizations = useMemo(() => orgsQuery.data ?? [], [orgsQuery.data]);

  useEffect(() => {
    if (!organizations.length) {
      return;
    }
    const stillValid = selectedId
      ? organizations.some((organization) => organization.id === selectedId)
      : false;
    if (!stillValid) {
      const nextId = organizations[0].id;
      setSelectedId(nextId);
      localStorage.setItem(SELECTED_ORG_KEY, nextId);
    }
  }, [organizations, selectedId]);

  const selectOrganization = useCallback((organizationId: string) => {
    setSelectedId(organizationId);
    localStorage.setItem(SELECTED_ORG_KEY, organizationId);
  }, []);

  const selectedOrganization =
    organizations.find((organization) => organization.id === selectedId) ?? null;

  const value = useMemo<OrganizationContextValue>(
    () => ({
      organizations,
      selectedOrganization,
      selectOrganization,
      isLoading: orgsQuery.isLoading,
      refreshOrganizations: orgsQuery.refetch,
    }),
    [
      organizations,
      orgsQuery.isLoading,
      orgsQuery.refetch,
      selectOrganization,
      selectedOrganization,
    ],
  );

  return <OrganizationContext.Provider value={value}>{children}</OrganizationContext.Provider>;
}

export function useOrganizationContext(): OrganizationContextValue {
  const context = useContext(OrganizationContext);
  if (!context) {
    throw new Error("useOrganizationContext must be used within OrganizationProvider");
  }
  return context;
}
