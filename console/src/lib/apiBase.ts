/** Resolve the control-plane origin for API calls. */
export function resolveApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
  if (typeof window === "undefined") {
    return configured.replace(/\/$/, "");
  }
  const host = window.location.hostname;
  if (host && host !== "localhost" && host !== "127.0.0.1") {
    return window.location.origin;
  }
  return configured.replace(/\/$/, "");
}
