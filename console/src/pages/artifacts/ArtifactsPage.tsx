import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";

import {
  listArtifacts,
  listDeviceTypes,
  type ArtifactListParams,
  type ArtifactType,
} from "@/api/fleet";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { ArtifactTable } from "@/components/artifacts/ArtifactTable";
import { ArtifactUploadDialog } from "@/components/artifacts/ArtifactUploadDialog";
import { ARTIFACT_TYPE_SECTION_LABELS, ARTIFACT_TYPES } from "@/components/artifacts/artifactTypes";
import { useArtifactActions } from "@/components/artifacts/useArtifactActions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs } from "@/components/ui/tabs";
import { canManageFleet } from "@/lib/permissions";

const PAGE_SIZE = 20;

type TypeTab = ArtifactType | "all";

const TYPE_TABS: { id: TypeTab; label: string }[] = [
  { id: "all", label: "All artifacts" },
  ...ARTIFACT_TYPES.map((value) => ({ id: value, label: ARTIFACT_TYPE_SECTION_LABELS[value] })),
];

function parseType(value: string | null): ArtifactType | undefined {
  return ARTIFACT_TYPES.find((item) => item === value);
}
const SELECT_CLASS = "h-10 rounded-md border border-input bg-field text-foreground px-3 text-sm";

export function ArtifactsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const type = parseType(searchParams.get("type"));
  const [deviceTypeId, setDeviceTypeId] = useState("");
  const [version, setVersion] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [showUpload, setShowUpload] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const typesQuery = useQuery({
    queryKey: ["device-types", organizationId, token],
    queryFn: () => listDeviceTypes(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const params: ArtifactListParams = {
    type,
    device_type_id: deviceTypeId || undefined,
    version: version || undefined,
    search: search || undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", organizationId, params, token],
    queryFn: () => listArtifacts(token!, organizationId, params),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageFleet(orgQuery.data?.current_user_role);
  const { onDownload, onDelete, refresh } = useArtifactActions(token, organizationId, setError);
  const deviceTypeNames = useMemo(
    () => new Map((typesQuery.data ?? []).map((item) => [item.id, item.name])),
    [typesQuery.data],
  );

  const total = artifactsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function updateFilter(apply: () => void) {
    setPage(1);
    apply();
  }

  function onTypeChange(next: TypeTab) {
    updateFilter(() => setSearchParams(next === "all" ? {} : { type: next }));
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Artifacts</h1>
          <p className="mt-2 text-muted-foreground">
            Versioned files for your device types: OS images, firmware, and more.
          </p>
        </div>
        {canManage && (
          <Button onClick={() => setShowUpload(true)}>
            {type === "os_image" ? "Upload OS image" : "Upload artifact"}
          </Button>
        )}
      </div>

      <Tabs tabs={TYPE_TABS} active={type ?? "all"} onChange={onTypeChange} label="Artifact type" />

      <div className="grid gap-3 rounded-lg border border-border bg-card p-4 shadow-glow md:grid-cols-3">
        <Input
          aria-label="Search artifacts"
          placeholder="Search name or file…"
          value={search}
          onChange={(event) => updateFilter(() => setSearch(event.target.value))}
        />
        <select
          aria-label="Filter by device type"
          className={SELECT_CLASS}
          value={deviceTypeId}
          onChange={(event) => updateFilter(() => setDeviceTypeId(event.target.value))}
        >
          <option value="">All device types</option>
          {(typesQuery.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        <Input
          aria-label="Filter by version"
          placeholder="Version, e.g. 1.1"
          value={version}
          onChange={(event) => updateFilter(() => setVersion(event.target.value))}
        />
      </div>

      {error && (
        <p className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">{error}</p>
      )}

      <ArtifactTable
        artifacts={artifactsQuery.data?.items ?? []}
        canManage={canManage}
        onDownload={onDownload}
        onDelete={onDelete}
        showType={!type}
        deviceTypeNames={deviceTypeNames}
        emptyMessage={artifactsQuery.isLoading ? "Loading artifacts…" : "No artifacts found."}
      />

      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{total} artifact(s)</p>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
          >
            Next
          </Button>
        </div>
      </div>

      {showUpload && token && (
        <ArtifactUploadDialog
          token={token}
          organizationId={organizationId}
          deviceTypes={typesQuery.data ?? []}
          defaultType={type ?? "os_image"}
          onClose={() => setShowUpload(false)}
          onUploaded={async () => {
            setShowUpload(false);
            await refresh();
          }}
        />
      )}
    </section>
  );
}
