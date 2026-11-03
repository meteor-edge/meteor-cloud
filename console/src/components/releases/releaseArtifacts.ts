import type { ArtifactType, ReleaseKind } from "@/api/fleet";

/** The artifact type each release kind installs. */
export const ARTIFACT_TYPE_FOR_KIND: Record<ReleaseKind, ArtifactType> = {
  systemd: "systemd",
  docker: "docker",
  docker_compose: "docker_compose",
};

/** The release kind that can install an artifact of this type, if any. */
export function releaseKindForArtifactType(type: ArtifactType): ReleaseKind | null {
  const match = (Object.keys(ARTIFACT_TYPE_FOR_KIND) as ReleaseKind[]).find(
    (kind) => ARTIFACT_TYPE_FOR_KIND[kind] === type,
  );
  return match ?? null;
}

export function releasesForArtifactPath(organizationId: string, artifactId: string): string {
  return `/organizations/${organizationId}/releases?artifact_id=${encodeURIComponent(artifactId)}`;
}

export function createReleaseFromArtifactPath(organizationId: string, artifactId: string): string {
  return `/organizations/${organizationId}/releases?create=1&artifact_id=${encodeURIComponent(artifactId)}`;
}

/** Releases list searched to one release name (all its versions). */
export function releasePath(organizationId: string, release: { name: string }): string {
  return `/organizations/${organizationId}/releases?search=${encodeURIComponent(release.name)}`;
}

export function deploymentsForReleasePath(organizationId: string, releaseId: string): string {
  return `/organizations/${organizationId}/deployments?release_id=${encodeURIComponent(releaseId)}`;
}

export function artifactPath(
  organizationId: string,
  artifact: { type: ArtifactType; name: string; version?: string },
): string {
  const query = new URLSearchParams({ type: artifact.type, search: artifact.name });
  if (artifact.version) query.set("version", artifact.version);
  return `/organizations/${organizationId}/artifacts?${query.toString()}`;
}
