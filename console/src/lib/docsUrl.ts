/** Public documentation site (separate Next.js app). Not the control-plane API. */
export function resolveDocsBaseUrl(): string {
  const configured = import.meta.env.VITE_DOCS_BASE_URL ?? "http://localhost:3000/docs";
  return configured.replace(/\/$/, "");
}
