/** Same rule as the server: a device type's slug without separators, then the hardware suffix. */
export function typedDefaultName(typeSlug: string, suffix: string): string {
  const kind = typeSlug.toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 40);
  return kind ? `${kind}-${suffix}` : "";
}
