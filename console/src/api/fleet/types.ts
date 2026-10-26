export type ConnectivityStatus = "online" | "offline" | "never_seen";

/** A hardware model (e.g. "Raspberry Pi 4"), not a physical device. */
export type DeviceType = {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  description: string | null;
  manufacturer: string | null;
  model: string | null;
  architecture: string | null;
  capabilities: Record<string, unknown>;
  metadata: Record<string, unknown>;
  device_count: number;
  artifact_count: number;
  created_at: string;
  updated_at: string;
};

/** A logical collection of devices (e.g. "Production"). */
export type DeviceGroup = {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  description: string | null;
  labels: Record<string, unknown>;
  metadata: Record<string, unknown>;
  device_count: number;
  created_at: string;
  updated_at: string;
};

export type ArtifactType =
  | "os_image"
  | "firmware"
  | "docker"
  | "docker_compose"
  | "systemd"
  | "configuration"
  | "other";

export type ReleaseKind = "systemd" | "docker" | "docker_compose";

export type Release = {
  id: string;
  organization_id: string;
  name: string;
  version: string;
  kind: ReleaseKind;
  artifact_id: string | null;
  description: string | null;
  spec: Record<string, unknown>;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ReleaseListParams = {
  kind?: ReleaseKind;
  version?: string;
  search?: string;
  page?: number;
  page_size?: number;
};

export type ReleaseCreatePayload = {
  name: string;
  version: string;
  kind: ReleaseKind;
  artifact_id?: string | null;
  description?: string | null;
  spec: Record<string, unknown>;
};

export type DeploymentCounts = {
  pending: number;
  queued: number;
  updating: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  skipped: number;
  total: number;
};

export type DeploymentDevice = {
  id: string;
  deployment_id: string;
  device_id: string;
  device_name: string | null;
  status: string;
  progress_percent: number | null;
  /** Runtime health the agent reports (healthy / unhealthy / unknown); null before any report. */
  health: string | null;
  reported_at: string | null;
  /** URL state, only when this Deploy created an endpoint for the device. */
  endpoint_availability: string | null;
  previous_desired_release_id: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type Deployment = {
  id: string;
  organization_id: string;
  release_id: string;
  release: ReleaseSummary | null;
  name: string | null;
  target: Record<string, unknown>;
  rollout: Record<string, unknown>;
  status: string;
  provider: string;
  provider_ref: string | null;
  counts: DeploymentCounts;
  created_by_user_id: string | null;
  rollback_of_deployment_id: string | null;
  created_at: string;
  updated_at: string;
  devices?: DeploymentDevice[] | null;
};

export type DeploymentListParams = {
  status?: string;
  release_id?: string;
  page?: number;
  page_size?: number;
};

export type DeploymentCreatePayload = {
  release_id: string;
  name?: string | null;
  target: {
    device_ids?: string[];
    group_ids?: string[];
    device_type_ids?: string[];
  };
  rollout?: {
    strategy?: string;
    batch_size?: number | null;
    delay_seconds?: number | null;
  };
  endpoint?: {
    hostname: string;
    port: number;
    protocol?: string;
  } | null;
};

export type ApplicationEndpoint = {
  id: string;
  organization_id: string;
  device_id: string;
  deployment_id: string | null;
  device_software_id: string | null;
  workload_key: string | null;
  hostname: string;
  protocol: string;
  port: number;
  enabled: boolean;
  availability: string;
  public_url: string;
  upstream_url: string | null;
  created_at: string;
  updated_at: string;
};

export type DeviceConnectivity = {
  device_id: string;
  organization_id: string;
  provider: string;
  wg_public_key: string | null;
  wg_address: string | null;
  status: string;
  last_handshake_at: string | null;
  connected: boolean;
  /** From recent evidence: the gateway's handshake, then the agent's check-in. */
  state?: "connected" | "reconnecting" | "offline" | "pending" | "revoked";
  agent_last_seen_at?: string | null;
  ssh_host_key_fingerprint?: string | null;
  /** "ca": short-lived certificates; "key": the shared gateway key. */
  ssh_auth?: "ca" | "key";
  last_ssh_ok_at?: string | null;
  last_ssh_error?: string | null;
  last_ssh_error_at?: string | null;
  server_address: string;
  ssh_user: string;
  ssh_port: number;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ShellSession = {
  session_id: string;
  ticket: string;
  websocket_path: string;
  expires_at: string;
  device_id: string;
};

export type LogSourceKind = "journal" | "systemd" | "docker" | "file";

export type LogSource = {
  kind: LogSourceKind;
  name: string;
  state?: string | null;
  project?: string | null;
  size?: number | null;
};

export type LogSources = {
  sources: LogSource[];
  /** Keys of MeteorCloud workloads: "systemd:<unit>", "docker:<name>", "compose:<project>". */
  deployed: string[];
  /** When the device last listed its sources (listings are reused for about 30 s). */
  listed_at: string | null;
  /** Only deployed workloads are shown; system logs need terminal access. */
  restricted: boolean;
};

export type LogSessionRequest = {
  kind: LogSourceKind;
  name: string;
  /** Compose project of a docker container; the device checks the container belongs to it. */
  project: string | null;
  lines: number;
  since: "15m" | "1h" | "6h" | "24h" | "7d" | null;
  grep: string | null;
  follow: boolean;
};

export type ReleaseSummary = {
  id: string;
  name: string;
  version: string;
  kind: ReleaseKind;
};

export type DeviceSoftware = {
  id: string;
  organization_id: string;
  device_id: string;
  workload_key: string;
  kind: ReleaseKind;
  desired_release_id: string | null;
  desired_revision: number;
  desired_spec: Record<string, unknown>;
  desired_release: ReleaseSummary | null;
  installed_release_id: string | null;
  installed_version: string | null;
  installed_release: ReleaseSummary | null;
  health: string;
  sync_status: string;
  progress_percent: number;
  attempt_count: number;
  last_error: string | null;
  reported_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Artifact = {
  id: string;
  organization_id: string;
  device_type_id: string | null;
  name: string;
  version: string;
  type: ArtifactType;
  description: string | null;
  file_name: string;
  content_type: string | null;
  size_bytes: number;
  checksum_sha256: string;
  metadata: Record<string, unknown>;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ArtifactListParams = {
  type?: ArtifactType;
  device_type_id?: string;
  version?: string;
  search?: string;
  page?: number;
  page_size?: number;
};

export type RegistrationToken = {
  id: string;
  organization_id: string;
  name: string;
  token_prefix: string;
  device_type_id: string | null;
  device_group_id: string | null;
  expires_at: string | null;
  max_uses: number | null;
  use_count: number;
  revoked_at: string | null;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
};

export type RegistrationTokenWithSecret = RegistrationToken & {
  token: string;
};

export type Device = {
  id: string;
  organization_id: string;
  name: string;
  device_type_id: string | null;
  device_group_id: string | null;
  is_enabled: boolean;
  status: ConnectivityStatus;
  serial_number: string | null;
  mac_addresses: string[];
  hostname: string | null;
  os_name: string | null;
  os_version: string | null;
  kernel_version: string | null;
  architecture: string | null;
  cpu_model: string | null;
  cpu_cores: number | null;
  memory_mb: number | null;
  labels: Record<string, unknown>;
  metadata: Record<string, unknown>;
  credential_prefix: string | null;
  last_seen_at: string | null;
  registered_at: string | null;
  created_at: string;
  updated_at: string;
  mqtt_configured: boolean;
  mqtt_status: "online" | "offline" | null;
  mqtt_status_at: string | null;
  mqtt_metrics: Record<string, unknown> | null;
  mqtt_metrics_at: string | null;
};

export type DeviceCredential = {
  device_id: string;
  token: string;
  credential_prefix: string;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type DeviceListParams = {
  search?: string;
  device_type_id?: string;
  device_group_id?: string;
  architecture?: string;
  enabled?: boolean;
  status?: ConnectivityStatus;
  sort?: "name" | "last_seen_at" | "created_at" | "registered_at";
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

export type EnrollmentApiKey = {
  id: string;
  organization_id: string;
  name: string;
  key_prefix: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
};

export type EnrollmentApiKeyWithSecret = EnrollmentApiKey & {
  api_key: string;
};

export type EnrollmentStatus = "pending" | "approved" | "rejected" | "expired";

export type DeviceEnrollmentRequest = {
  id: string;
  organization_id: string;
  status: EnrollmentStatus;
  claim_secret_prefix: string;
  requested_name: string | null;
  assigned_name: string | null;
  device_type_id: string | null;
  device_group_id: string | null;
  serial_number: string | null;
  mac_addresses: string[];
  hostname: string | null;
  os_name: string | null;
  os_version: string | null;
  kernel_version: string | null;
  architecture: string | null;
  cpu_model: string | null;
  cpu_cores: number | null;
  memory_mb: number | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  rejection_reason: string | null;
  claimed_at: string | null;
  device_id: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
};
