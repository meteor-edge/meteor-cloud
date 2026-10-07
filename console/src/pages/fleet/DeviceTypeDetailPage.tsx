import { FormEvent, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  deleteDeviceType,
  getDeviceType,
  listArtifacts,
  listDeviceGroups,
  updateDeviceType,
  type Artifact,
  type ArtifactType,
  type DeviceType,
} from "@/api/fleet";
import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { ArtifactTable } from "@/components/artifacts/ArtifactTable";
import { ArtifactUploadDialog } from "@/components/artifacts/ArtifactUploadDialog";
import { ARTIFACT_TYPE_SECTION_LABELS, ARTIFACT_TYPES } from "@/components/artifacts/artifactTypes";
import { useArtifactActions } from "@/components/artifacts/useArtifactActions";
import { DeviceListTable } from "@/components/fleet/DeviceListTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs } from "@/components/ui/tabs";
import { canManageFleet } from "@/lib/permissions";
import { formatDateTime } from "@/lib/utils";

type Tab = "overview" | "devices" | "artifacts";
const TABS: Tab[] = ["overview", "devices", "artifacts"];

function Detail({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm">
        {value === null || value === undefined || value === "" ? "—" : value}
      </p>
    </div>
  );
}

export function DeviceTypeDetailPage() {
  const { organizationId = "", typeId = "" } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [uploadType, setUploadType] = useState<ArtifactType | null>(null);

  const requestedTab = searchParams.get("tab") as Tab | null;
  const tab: Tab = requestedTab && TABS.includes(requestedTab) ? requestedTab : "overview";

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const typeQuery = useQuery({
    queryKey: ["device-type", organizationId, typeId, token],
    queryFn: () => getDeviceType(token!, organizationId, typeId),
    enabled: Boolean(token && organizationId && typeId),
  });
  const groupsQuery = useQuery({
    queryKey: ["device-groups", organizationId, token],
    queryFn: () => listDeviceGroups(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", organizationId, { device_type_id: typeId }, token],
    queryFn: () =>
      listArtifacts(token!, organizationId, { device_type_id: typeId, page_size: 100 }),
    enabled: Boolean(token && organizationId && typeId),
  });

  const canManage = canManageFleet(orgQuery.data?.current_user_role);
  const { onDownload, onDelete, refresh } = useArtifactActions(token, organizationId, setError);

  const groupNames = useMemo(
    () => new Map((groupsQuery.data ?? []).map((group) => [group.id, group.name])),
    [groupsQuery.data],
  );
  const artifactsByType = useMemo(() => {
    const grouped = new Map<ArtifactType, Artifact[]>();
    for (const artifact of artifactsQuery.data?.items ?? []) {
      grouped.set(artifact.type, [...(grouped.get(artifact.type) ?? []), artifact]);
    }
    return grouped;
  }, [artifactsQuery.data]);

  if (typeQuery.isLoading) {
    return <p className="text-muted-foreground">Loading device type…</p>;
  }
  if (!typeQuery.data) {
    return (
      <p className="rounded-md bg-ember px-2 py-1 text-midnight">Device type was not found.</p>
    );
  }
  const deviceType = typeQuery.data;

  function selectTab(next: Tab) {
    setSearchParams(next === "overview" ? {} : { tab: next }, { replace: true });
  }

  // OS images are always listed (the primary upload path); other types only once they exist.
  const artifactSections = ARTIFACT_TYPES.filter(
    (type) => type === "os_image" || artifactsByType.has(type),
  );

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">Device Type</p>
          <h1 className="text-3xl font-semibold tracking-tight">{deviceType.name}</h1>
          <p className="mt-2 text-muted-foreground">
            {[deviceType.manufacturer, deviceType.model, deviceType.architecture]
              .filter(Boolean)
              .join(" · ") || deviceType.slug}
          </p>
        </div>
        <Button variant="secondary" asChild>
          <Link to={`/organizations/${organizationId}/device-types`}>Back to device types</Link>
        </Button>
      </div>

      <Tabs
        label="Device type sections"
        active={tab}
        onChange={selectTab}
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "devices", label: "Devices", count: deviceType.device_count },
          { id: "artifacts", label: "Artifacts", count: deviceType.artifact_count },
        ]}
      />

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}

      {tab === "overview" && (
        <DeviceTypeOverview
          key={deviceType.updated_at}
          deviceType={deviceType}
          canManage={canManage}
          onError={setError}
          onSaved={() =>
            Promise.all([
              queryClient.invalidateQueries({ queryKey: ["device-type", organizationId, typeId] }),
              queryClient.invalidateQueries({ queryKey: ["device-types", organizationId] }),
            ])
          }
          onDelete={async () => {
            if (!window.confirm(`Delete device type "${deviceType.name}"?`)) {
              return;
            }
            setError(null);
            try {
              await deleteDeviceType(token!, organizationId, typeId);
              await queryClient.invalidateQueries({ queryKey: ["device-types", organizationId] });
              navigate(`/organizations/${organizationId}/device-types`);
            } catch (err) {
              setError(err instanceof ApiError ? err.message : "Could not delete device type.");
            }
          }}
          token={token!}
          organizationId={organizationId}
        />
      )}

      {tab === "devices" && token && (
        <DeviceListTable
          token={token}
          organizationId={organizationId}
          filter={{ device_type_id: typeId }}
          secondaryColumn={{ label: "Device Group", names: groupNames, key: "device_group_id" }}
        />
      )}

      {tab === "artifacts" && (
        <div className="space-y-8">
          {canManage && (
            <div className="flex justify-end">
              <Button variant="secondary" onClick={() => setUploadType("firmware")}>
                Upload other artifact
              </Button>
            </div>
          )}
          {artifactSections.map((type) => (
            <div key={type} className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">{ARTIFACT_TYPE_SECTION_LABELS[type]}</h2>
                {canManage && type === "os_image" && (
                  <Button onClick={() => setUploadType("os_image")}>Upload OS image</Button>
                )}
              </div>
              <ArtifactTable
                artifacts={artifactsByType.get(type) ?? []}
                canManage={canManage}
                onDownload={onDownload}
                onDelete={onDelete}
                emptyMessage={
                  artifactsQuery.isLoading
                    ? "Loading…"
                    : `No ${ARTIFACT_TYPE_SECTION_LABELS[type]} for ${deviceType.name} yet.`
                }
              />
            </div>
          ))}
          {artifactsQuery.data && artifactsQuery.data.total > artifactsQuery.data.items.length && (
            <p className="text-sm text-muted-foreground">
              Showing {artifactsQuery.data.items.length} of {artifactsQuery.data.total} artifacts.{" "}
              <Link to={`/organizations/${organizationId}/artifacts`} className="text-link">
                View all in Artifacts
              </Link>
            </p>
          )}
        </div>
      )}

      {uploadType && token && (
        <ArtifactUploadDialog
          token={token}
          organizationId={organizationId}
          deviceTypes={[deviceType]}
          fixedDeviceType={deviceType}
          defaultType={uploadType}
          onClose={() => setUploadType(null)}
          onUploaded={async () => {
            setUploadType(null);
            await refresh();
          }}
        />
      )}
    </section>
  );
}

function DeviceTypeOverview({
  deviceType,
  canManage,
  token,
  organizationId,
  onSaved,
  onDelete,
  onError,
}: {
  deviceType: DeviceType;
  canManage: boolean;
  token: string;
  organizationId: string;
  onSaved: () => Promise<unknown>;
  onDelete: () => void;
  onError: (message: string | null) => void;
}) {
  const [form, setForm] = useState({
    name: deviceType.name,
    manufacturer: deviceType.manufacturer ?? "",
    model: deviceType.model ?? "",
    architecture: deviceType.architecture ?? "",
    description: deviceType.description ?? "",
  });
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    onError(null);
    setSaving(true);
    try {
      await updateDeviceType(token, organizationId, deviceType.id, {
        name: form.name,
        manufacturer: form.manufacturer || null,
        model: form.model || null,
        architecture: form.architecture || null,
        description: form.description || null,
      });
      await onSaved();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not update device type.");
    } finally {
      setSaving(false);
    }
  }

  const fields: Array<{ key: keyof typeof form; label: string }> = [
    { key: "name", label: "Name" },
    { key: "manufacturer", label: "Manufacturer" },
    { key: "model", label: "Model" },
    { key: "architecture", label: "Architecture" },
  ];

  return (
    <div className="space-y-6">
      <div className="grid gap-4 rounded-lg border border-border bg-card p-6 shadow-glow sm:grid-cols-3">
        <Detail label="Devices" value={deviceType.device_count} />
        <Detail label="Artifacts" value={deviceType.artifact_count} />
        <Detail label="Slug" value={deviceType.slug} />
        <Detail label="Manufacturer" value={deviceType.manufacturer} />
        <Detail label="Model" value={deviceType.model} />
        <Detail label="Architecture" value={deviceType.architecture} />
        <Detail label="Created" value={formatDateTime(deviceType.created_at)} />
        <div className="sm:col-span-2">
          <Detail label="Description" value={deviceType.description} />
        </div>
      </div>

      {canManage && (
        <form
          className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-glow"
          onSubmit={onSubmit}
        >
          <h2 className="text-lg font-semibold">Edit device type</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {fields.map((field) => (
              <div key={field.key}>
                <Label htmlFor={`edit-${field.key}`}>{field.label}</Label>
                <Input
                  id={`edit-${field.key}`}
                  value={form[field.key]}
                  required={field.key === "name"}
                  onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
                />
              </div>
            ))}
            <div className="md:col-span-2">
              <Label htmlFor="edit-description">Description</Label>
              <Input
                id="edit-description"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />
            </div>
          </div>
          <div className="flex flex-wrap justify-between gap-2">
            <Button type="submit" disabled={saving}>
              Save changes
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="bg-ember text-midnight hover:bg-ember hover:text-midnight"
              onClick={onDelete}
            >
              Delete device type
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
