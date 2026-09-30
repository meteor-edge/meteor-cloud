import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchCurrentUser, loginUser, registerUser, type User } from "@/api/auth";
import { ApiError } from "@/api/http";
import { clearStoredToken, getStoredToken, storeToken } from "@/auth/token";

type AuthContextValue = {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, fullName: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null>(() => getStoredToken());

  const meQuery = useQuery({
    queryKey: ["auth", "me", token],
    queryFn: () => fetchCurrentUser(token!),
    enabled: Boolean(token),
    retry: false,
  });

  useEffect(() => {
    if (meQuery.error instanceof ApiError && meQuery.error.status === 401) {
      clearStoredToken();
      setToken(null);
    }
  }, [meQuery.error]);

  const login = useCallback(
    async (email: string, password: string) => {
      const response = await loginUser({ email, password });
      storeToken(response.access_token);
      setToken(response.access_token);
      await queryClient.fetchQuery({
        queryKey: ["auth", "me", response.access_token],
        queryFn: () => fetchCurrentUser(response.access_token),
      });
    },
    [queryClient],
  );

  const register = useCallback(async (email: string, fullName: string, password: string) => {
    await registerUser({ email, full_name: fullName, password });
  }, []);

  const logout = useCallback(() => {
    clearStoredToken();
    setToken(null);
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user: meQuery.data ?? null,
      token,
      isLoading: Boolean(token) && meQuery.isLoading,
      isAuthenticated: Boolean(token && meQuery.data),
      login,
      register,
      logout,
    }),
    [login, logout, meQuery.data, meQuery.isLoading, register, token],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
