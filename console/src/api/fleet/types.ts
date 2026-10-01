export type ConnectivityStatus = "online" | "offline" | "never_seen";

export type DeviceType = {
  id: string;
  organization_id: string;
  name: string;
  description: string | null;
  capabilities: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type DeviceGroup = {
  id: string;
  organization_id: string;
  name: string;
  description: string | null;
  labels: Record<string, unknown>;
  created_at: string;
  updated_at: string;
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
