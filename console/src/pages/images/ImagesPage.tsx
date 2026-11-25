import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/http";
import { getOrganization } from "@/api/organizations";
import {
  deleteImage,
  getRegistry,
  listImages,
  listImageTags,
  type ImageRepository,
  type ImageTag,
} from "@/api/registry";
import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { canDeleteImages } from "@/lib/permissions";
import { formatBytes, formatDateTime } from "@/lib/utils";

const PAGE_SIZE = 20;

function platformLabel(platform: ImageTag["platforms"][number]): string {
  return [platform.os, platform.architecture, platform.variant].filter(Boolean).join("/");
}

function Pager({
  total,
  page,
  onChange,
}: {
  total: number;
  page: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.ceil(total / PAGE_SIZE);
  // Deleting the last item of the last page leaves `page` past the end: keep a way back.
  if (pages <= 1 && page <= 1) return null;
  return (
    <div className="flex items-center gap-3 text-sm text-muted-foreground">
      <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => onChange(Math.max(1, Math.min(page - 1, pages)))}>
        Previous
      </Button>
      Page {page} of {Math.max(1, pages)}
      <Button
        variant="secondary"
        size="sm"
        disabled={page >= pages}
        onClick={() => onChange(page + 1)}
      >
        Next
      </Button>
    </div>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setFailed(false);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Not available on plain http: show the text to copy by hand.
      setFailed(true);
    }
  }
  if (failed) return <code className="select-all break-all text-xs">{value}</code>;
  return (
    <Button variant="ghost" size="sm" onClick={copy} aria-label={label}>
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function Tags({
  organizationId,
  repository,
  pullName,
  canDelete,
  onDelete,
}: {
  organizationId: string;
  repository: string;
  pullName: string;
  canDelete: boolean;
  onDelete: (repository: string, tag: string) => void;
}) {
  const { token } = useAuth();
  const [page, setPage] = useState(1);
  const tagsQuery = useQuery({
    queryKey: ["image-tags", organizationId, repository, page, token],
    queryFn: () => listImageTags(token!, organizationId, repository, page, PAGE_SIZE),
    enabled: Boolean(token),
    refetchInterval: 15_000,
  });
  if (tagsQuery.isError) {
    return <p className="p-4 text-sm">Could not load the tags.</p>;
  }
  return (
    <div className="space-y-3 border-t border-border p-4">
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              {["Tag", "Digest", "Platforms", "Size", "Pushed", "Pull", ""].map((heading) => (
                <th key={heading} className="px-3 py-2 font-semibold">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(tagsQuery.data?.items ?? []).map((image) => (
              <tr key={image.tag} className="border-t border-border">
                <td className="px-3 py-2 font-medium">{image.tag}</td>
                <td className="px-3 py-2 font-mono text-xs" title={image.digest}>
                  {image.digest.slice(0, 19)}…
                </td>
                <td className="px-3 py-2">
                  {image.platforms.map(platformLabel).join(", ") || "Unknown"}
                </td>
                <td className="whitespace-nowrap px-3 py-2">{formatBytes(image.size_bytes)}</td>
                <td className="px-3 py-2 text-muted-foreground">
                  {formatDateTime(image.pushed_at)}
                  {image.pushed_by && ` by ${image.pushed_by}`}
                </td>
                <td className="px-3 py-2">
                  <CopyButton
                    value={`docker pull ${pullName}:${image.tag}`}
                    label={`Copy pull command for ${repository}:${image.tag}`}
                  />
                </td>
                <td className="px-3 py-2">
                  {canDelete && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Delete ${repository}:${image.tag}`}
                      onClick={() => onDelete(repository, image.tag)}
                    >
                      Delete
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager total={tagsQuery.data?.total ?? 0} page={page} onChange={setPage} />
    </div>
  );
}

export function ImagesPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const enabled = Boolean(token && organizationId);
  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled,
  });
  const registryQuery = useQuery({
    queryKey: ["registry", organizationId, token],
    queryFn: () => getRegistry(token!, organizationId),
    enabled,
  });
  const registry = registryQuery.data;
  const imagesQuery = useQuery({
    queryKey: ["images", organizationId, search, page, token],
    queryFn: () => listImages(token!, organizationId, { search, page, page_size: PAGE_SIZE }),
    enabled: enabled && Boolean(registry?.enabled),
    // A push shows up without reloading the page.
    refetchInterval: 15_000,
  });

  const canDelete = canDeleteImages(orgQuery.data?.current_user_role);
  const base = `${registry?.host}/${registry?.namespace}`;
  const images: ImageRepository[] = imagesQuery.data?.items ?? [];

  async function onDelete(repository: string, tag?: string) {
    const label = tag ? `${repository}:${tag}` : `${repository} and all its tags`;
    if (!window.confirm(`Delete ${label}?`)) return;
    setError(null);
    try {
      await deleteImage(token!, organizationId, repository, tag);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the image.");
    }
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["images", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["image-tags", organizationId] }),
    ]);
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Images</h1>
          <p className="mt-2 text-muted-foreground">
            Container images pushed to this organization&apos;s registry.
          </p>
        </div>
        {registry?.enabled && (
          <Button variant="secondary" asChild>
            <Link to={`/organizations/${organizationId}/registry`}>Registry tokens</Link>
          </Button>
        )}
      </div>

      {(error || registryQuery.isError || imagesQuery.isError) && (
        <p role="alert" className="rounded-md bg-ember px-2 py-1 text-sm font-medium text-midnight">
          {error ?? "Could not load the images."}
        </p>
      )}

      {registry && !registry.enabled && (
        <p className="rounded-lg border border-border bg-card p-5 text-sm text-muted-foreground">
          The container registry is not enabled on this platform.
        </p>
      )}

      {registry?.enabled && (
        <>
          <Input
            className="max-w-xs"
            placeholder="Search by name"
            aria-label="Search images"
            value={search}
            onChange={(event) => {
              setPage(1);
              setSearch(event.target.value);
            }}
          />
          {imagesQuery.isSuccess && images.length === 0 && search && (
            <p className="text-sm text-muted-foreground">No image matches.</p>
          )}
          {imagesQuery.isSuccess && images.length === 0 && !search && (
            <div className="rounded-lg border border-border bg-card p-5 shadow-glow">
              <p className="text-sm font-medium">No images yet. Push your first one:</p>
              <pre className="mt-2 overflow-x-auto rounded-md border border-border bg-background px-3 py-2 text-xs">
                {[
                  `docker login ${registry.host}`,
                  `docker tag my-app ${base}/my-app:1.0`,
                  `docker push ${base}/my-app:1.0`,
                ].join("\n")}
              </pre>
              <p className="mt-2 text-xs text-muted-foreground">
                The password is a registry token. The image appears here after the push.
              </p>
            </div>
          )}
          <ul className="space-y-3">
            {images.map((image) => (
              <li
                key={image.repository}
                className="rounded-lg border border-border bg-card shadow-glow"
              >
                <div className="flex flex-wrap items-center justify-between gap-3 p-4">
                  <button
                    type="button"
                    className="text-left font-medium hover:underline"
                    aria-expanded={open === image.repository}
                    onClick={() => setOpen(open === image.repository ? null : image.repository)}
                  >
                    {image.repository}
                  </button>
                  <span className="text-sm text-muted-foreground">
                    {image.tag_count} {image.tag_count === 1 ? "tag" : "tags"} · pushed{" "}
                    {formatDateTime(image.pushed_at)}
                  </span>
                  {canDelete && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Delete ${image.repository}`}
                      onClick={() => onDelete(image.repository)}
                    >
                      Delete
                    </Button>
                  )}
                </div>
                {open === image.repository && (
                  <Tags
                    organizationId={organizationId}
                    repository={image.repository}
                    pullName={`${base}/${image.repository}`}
                    canDelete={canDelete}
                    onDelete={onDelete}
                  />
                )}
              </li>
            ))}
          </ul>
          <Pager total={imagesQuery.data?.total ?? 0} page={page} onChange={setPage} />
        </>
      )}
    </section>
  );
}
