import type { ArtifactType } from "@/api/fleet";

/** Display order: OS images first since they are the primary artifact today. */
export const ARTIFACT_TYPES: ArtifactType[] = [
  "os_image",
  "firmware",
  "docker_compose",
  "systemd",
  "configuration",
  "other",
];

export const ARTIFACT_TYPE_LABELS: Record<ArtifactType, string> = {
  os_image: "OS Image",
  firmware: "Firmware",
  docker_compose: "Docker Compose",
  systemd: "systemd",
  configuration: "Configuration",
  other: "Other",
};

export const ARTIFACT_TYPE_SECTION_LABELS: Record<ArtifactType, string> = {
  os_image: "OS Images",
  firmware: "Firmware",
  docker_compose: "Docker Compose",
  systemd: "systemd Units",
  configuration: "Configuration",
  other: "Other",
};
