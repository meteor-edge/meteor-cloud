import { apiRequest } from "@/api/http";

export type User = {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
};

export type RegisterPayload = {
  email: string;
  full_name: string;
  password: string;
};

export type LoginPayload = {
  email: string;
  password: string;
};

export function registerUser(payload: RegisterPayload): Promise<User> {
  return apiRequest<User>("/api/v1/auth/register", { body: payload });
}

export function loginUser(payload: LoginPayload): Promise<TokenResponse> {
  return apiRequest<TokenResponse>("/api/v1/auth/login", { body: payload });
}

export function fetchCurrentUser(token: string): Promise<User> {
  return apiRequest<User>("/api/v1/auth/me", { token });
}
