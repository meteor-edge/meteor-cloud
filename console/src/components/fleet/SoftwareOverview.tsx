import type { DeviceSoftwareDetail, SnapshotContainer, SoftwareActionKind } from "@/api/fleet";
import { Button } from "@/components/ui/button";
import { cn, formatBytes, formatDateTime, formatDuration } from "@/lib/utils";

export type ServiceOrder = (kind: SoftwareActionKind, service: string) => void;

function time(value: string): string {
  // Docker writes 0001-01-01 for "never".
  const at = Date.parse(value);
  return Number.isNaN(at) || at <= 0 ? "—" : formatDateTime(value);
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-lg border border-border bg-card p-4 shadow-glow sm:p-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">{value || "—"}</dd>
    </div>
  );
}

function ports(container: SnapshotContainer): string {
  return container.ports
    .map((p) =>
      p.host_port
        ? `${p.host_ip && p.host_ip !== "0.0.0.0" ? `${p.host_ip}:` : ""}${p.host_port} → ${p.container}`
        : p.container,
    )
    .join(", ");
}

function ContainerCard({
  container,
  compose,
  stopped,
  onService,
}: {
  container: SnapshotContainer;
  compose: boolean;
  stopped: boolean;
  onService?: ServiceOrder;
}) {
  const bad = container.health === "unhealthy" || container.oom_killed;
  return (
    <li className="space-y-3 rounded-md border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="break-words font-medium">{container.service || container.name}</p>
          {container.service && (
            <p className="break-words text-xs text-muted-foreground">{container.name}</p>
          )}
        </div>
        <p
          className={cn(
            "text-sm",
            container.running && !bad ? "text-aurora" : "text-muted-foreground",
            bad && "text-ember",
          )}
        >
          {container.state}
          {container.health && container.health !== "none" ? ` · ${container.health}` : ""}
          {container.oom_killed ? " · out of memory" : ""}
          {stopped ? " · stopped by user" : ""}
        </p>
      </div>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Image" value={container.image} />
        <Fact label="Started" value={time(container.started_at)} />
        <Fact label="Restarts" value={String(container.restarts)} />
        {!container.running && (
          <Fact
            label="Last exit"
            value={`code ${container.exit_code} · ${time(container.finished_at)}`}
          />
        )}
        <Fact label="Ports" value={ports(container)} />
        <Fact label="Networks" value={container.networks.join(", ")} />
        {container.mounts.length > 0 && (
          <Fact
            label="Mounts"
            value={
              <ul>
                {container.mounts.map((m) => (
                  <li key={m.destination}>
                    {m.name || m.source || m.type} → {m.destination}
                    {m.rw ? "" : " (read-only)"}
                  </li>
                ))}
              </ul>
            }
          />
        )}
      </dl>
      {compose && onService && container.service && (
        <div className="flex flex-wrap gap-2">
          {container.running && !stopped ? (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => onService("workload_restart", container.service)}
              >
                Restart service
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => onService("workload_stop", container.service)}
              >
                Stop service
              </Button>
            </>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => onService("workload_start", container.service)}
            >
              Start service
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

/** What the workload is made of: containers or the systemd unit, volumes, networks, images. */
export function SoftwareOverview({
  software,
  onService,
}: {
  software: DeviceSoftwareDetail;
  /** Set when the user may manage services one by one. */
  onService?: ServiceOrder;
}) {
  const snapshot = software.snapshot;
  if (!snapshot) {
    return (
      <p className="rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
        The device has not reported details of this software yet. That needs a current meteorcli;
        update it on the device page.
      </p>
    );
  }
  const unit = snapshot.unit;
  return (
    <div className="space-y-6">
      {snapshot.note && <p className="text-sm text-muted-foreground">{snapshot.note}</p>}
      {snapshot.truncated && (
        <p className="text-sm text-muted-foreground">
          This software is too large to show completely; some parts are left out.
        </p>
      )}
      {snapshot.kind !== "systemd" && (
        <Panel title={snapshot.kind === "docker_compose" ? "Services" : "Container"}>
          {snapshot.containers.length === 0 ? (
            <p className="text-sm text-muted-foreground">No containers exist right now.</p>
          ) : (
            <ul className="space-y-3">
              {snapshot.containers.map((c) => (
                <ContainerCard
                  key={c.id || c.name}
                  container={c}
                  compose={snapshot.kind === "docker_compose"}
                  stopped={
                    Boolean(software.stopped_by_user) &&
                    (snapshot.stopped_services.length === 0 ||
                      snapshot.stopped_services.includes(c.service))
                  }
                  onService={onService}
                />
              ))}
            </ul>
          )}
        </Panel>
      )}
      {unit && (
        <Panel title="Unit">
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="State" value={`${unit.active_state} (${unit.sub_state})`} />
            <Fact label="At boot" value={unit.enabled} />
            <Fact label="Active since" value={time(unit.active_since)} />
            <Fact label="Restarts" value={String(unit.restarts)} />
            <Fact label="Last result" value={`${unit.result} · exit ${unit.exit_status}`} />
            {unit.memory_bytes != null && (
              <Fact label="Memory" value={formatBytes(unit.memory_bytes)} />
            )}
            {unit.cpu_seconds != null && (
              <Fact label="CPU time" value={formatDuration(Math.round(unit.cpu_seconds))} />
            )}
          </dl>
        </Panel>
      )}
      {snapshot.volumes.length > 0 && (
        <Panel title="Volumes">
          <ul className="space-y-1 text-sm">
            {snapshot.volumes.map((v) => (
              <li key={v.name} className="break-words">
                {v.name} <span className="text-muted-foreground">{v.driver}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      {snapshot.networks.length > 0 && (
        <Panel title="Networks">
          <ul className="space-y-1 text-sm">
            {snapshot.networks.map((n) => (
              <li key={n.name} className="break-words">
                {n.name} <span className="text-muted-foreground">{n.driver}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      {snapshot.images.length > 0 && (
        <Panel title="Images">
          <ul className="space-y-2 text-sm">
            {snapshot.images.map((i) => (
              <li key={i.id} className="break-all">
                {i.tags.join(", ") || i.id}{" "}
                <span className="text-muted-foreground">
                  {formatBytes(i.size)} · built {time(i.created)}
                </span>
                {i.digests.length > 0 && (
                  <p className="text-xs text-muted-foreground">{i.digests.join(", ")}</p>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
