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

function ago(value: string, now: number): string | null {
  const at = Date.parse(value);
  if (Number.isNaN(at) || at <= 0) {
    return null;
  }
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  const days = Math.floor(seconds / 86_400);
  return days >= 2 ? `${days} days` : formatDuration(seconds);
}

/** Like the STATUS column of docker ps. Without ``live`` the device may have changed since. */
function status(c: SnapshotContainer, now: number, live: boolean): string {
  const health = c.health && c.health !== "none" ? ` (${c.health})` : "";
  if (c.running) {
    const up = live ? ago(c.started_at, now) : null;
    return `${c.state === "running" ? "Up" : c.state}${up ? ` ${up}` : ""}${health}`;
  }
  if (c.state === "exited" || c.state === "dead") {
    const since = ago(c.finished_at, now);
    return `Exited (${c.exit_code})${since ? ` ${since} ago` : ""}`;
  }
  return c.state;
}

/** The workload's containers like docker ps, plus what they use. */
function ContainerTable({
  containers,
  now,
  live,
}: {
  containers: SnapshotContainer[];
  now: number;
  live: boolean;
}) {
  const heads = [
    "Name",
    "Image",
    "Status",
    "Created",
    "Ports",
    "CPU",
    "Memory",
    "Processes",
    "Disk",
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] text-left text-sm">
        <thead className="text-xs text-muted-foreground">
          <tr>
            {heads.map((head) => (
              <th key={head} className="py-1.5 pr-3 font-medium">
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {containers.map((c) => {
            const created = c.created_at ? ago(c.created_at, now) : null;
            return (
              <tr key={c.id || c.name} className="border-t border-border align-top tabular-nums">
                <td className="py-1.5 pr-3 font-medium">{c.name}</td>
                <td className="break-all py-1.5 pr-3">{c.image || "—"}</td>
                <td
                  className={cn(
                    "py-1.5 pr-3",
                    c.running && c.health !== "unhealthy" ? "text-aurora" : "text-ember",
                  )}
                >
                  {status(c, now, live)}
                </td>
                <td className="whitespace-nowrap py-1.5 pr-3">
                  {created ? `${created} ago` : "—"}
                </td>
                <td className="py-1.5 pr-3">{ports(c) || "—"}</td>
                <td className="py-1.5 pr-3">
                  {c.stats ? `${c.stats.cpu_percent.toFixed(1)}%` : "—"}
                </td>
                <td className="whitespace-nowrap py-1.5 pr-3">
                  {c.stats && c.stats.memory_bytes > 0 ? formatBytes(c.stats.memory_bytes) : "—"}
                </td>
                <td className="py-1.5 pr-3">{c.stats ? c.stats.pids : "—"}</td>
                <td className="whitespace-nowrap py-1.5">
                  {c.size_rw != null ? formatBytes(c.size_rw) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type Stats = NonNullable<SnapshotContainer["stats"]>;

function Meter({ label, percent, text }: { label: string; percent: number; text: string }) {
  const width = Math.min(100, Math.max(0, percent));
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">{text}</span>
      </div>
      <div
        className="mt-1 h-1.5 overflow-hidden rounded bg-muted"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(width)}
      >
        <div
          className={cn("h-full", width >= 90 ? "bg-ember" : "bg-aurora")}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

/** CPU (100% is one core), memory against its limit, network, disk and processes. */
function Usage({ container, stats }: { container: SnapshotContainer; stats: Stats }) {
  const cores = container.nano_cpus > 0 ? container.nano_cpus / 1e9 : 1;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Meter
        label="CPU"
        percent={stats.cpu_percent / cores}
        text={`${stats.cpu_percent.toFixed(1)}%${container.nano_cpus > 0 ? ` of ${cores} CPU` : ""}`}
      />
      {stats.memory_bytes > 0 ? (
        <Meter
          label="Memory"
          percent={stats.memory_percent}
          text={`${formatBytes(stats.memory_bytes)}${
            container.memory_limit > 0 ? ` of ${formatBytes(container.memory_limit)}` : ""
          }`}
        />
      ) : (
        // No running container uses 0 B: the kernel does not count (Raspberry Pi OS default).
        <Fact label="Memory" value="Not measured on this device" />
      )}
      <Fact
        label="Network in / out"
        value={`${formatBytes(stats.net_rx_bytes)} / ${formatBytes(stats.net_tx_bytes)}`}
      />
      <Fact
        label="Disk read / written"
        value={`${formatBytes(stats.block_read_bytes)} / ${formatBytes(stats.block_write_bytes)}`}
      />
    </div>
  );
}

/** What the workload uses in total, and what Docker keeps on the device. */
function Resources({ software }: { software: DeviceSoftwareDetail }) {
  const snapshot = software.snapshot!;
  const stats = snapshot.containers.flatMap((c) => (c.stats ? [c.stats] : []));
  const sum = (pick: (s: Stats) => number) => stats.reduce((n, s) => n + pick(s), 0);
  const sized = snapshot.volumes.filter((v) => v.size != null);
  const disk = snapshot.disk;
  if (stats.length === 0 && sized.length === 0 && !disk) {
    return null;
  }
  const reclaimable = disk?.items.reduce((n, i) => n + i.reclaimable, 0) ?? 0;
  return (
    <Panel title="Resources">
      <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Fact
          label="Containers running"
          value={`${snapshot.containers.filter((c) => c.running).length} of ${snapshot.containers.length}`}
        />
        {stats.length > 0 && (
          <>
            <Fact label="CPU (100% = 1 core)" value={`${sum((s) => s.cpu_percent).toFixed(1)}%`} />
            {stats.every((s) => s.memory_bytes > 0) && (
              <Fact label="Memory" value={formatBytes(sum((s) => s.memory_bytes))} />
            )}
            <Fact label="Processes" value={String(sum((s) => s.pids))} />
          </>
        )}
        {snapshot.images.length > 0 && (
          <Fact
            label="Images"
            value={formatBytes(snapshot.images.reduce((n, i) => n + i.size, 0))}
          />
        )}
        {sized.length > 0 && (
          <Fact label="Volumes" value={formatBytes(sized.reduce((n, v) => n + (v.size ?? 0), 0))} />
        )}
      </dl>
      {disk && disk.items.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Docker storage on this device, all software: {formatBytes(reclaimable)} reclaimable, as
            Docker counts it. Measured {time(disk.taken_at)}.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1.5 pr-3 font-medium">Type</th>
                  <th className="py-1.5 pr-3 font-medium">In use</th>
                  <th className="py-1.5 pr-3 font-medium">Size</th>
                  <th className="py-1.5 font-medium">Reclaimable</th>
                </tr>
              </thead>
              <tbody>
                {disk.items.map((i) => (
                  <tr key={i.type} className="border-t border-border tabular-nums">
                    <td className="py-1.5 pr-3">{i.type}</td>
                    <td className="py-1.5 pr-3">
                      {i.active} of {i.total}
                    </td>
                    <td className="py-1.5 pr-3">{formatBytes(i.size)}</td>
                    <td className="py-1.5">{formatBytes(i.reclaimable)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
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
      {container.stats && <Usage container={container} stats={container.stats} />}
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
        {container.restart_policy && (
          <Fact label="Restart policy" value={container.restart_policy} />
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
  live,
  onService,
}: {
  software: DeviceSoftwareDetail;
  /** The device is online, so what ran then still counts up. */
  live: boolean;
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
  // The page asks again every few seconds, so this moves on.
  const now = Date.now();
  const unitUp = live && unit?.active_state === "active" ? ago(unit.active_since, now) : null;
  return (
    <div className="space-y-6">
      {snapshot.note && <p className="text-sm text-muted-foreground">{snapshot.note}</p>}
      {snapshot.truncated && (
        <p className="text-sm text-muted-foreground">
          This software is too large to show completely; some parts are left out.
        </p>
      )}
      <Resources software={software} />
      {snapshot.kind !== "systemd" && (
        <Panel title={snapshot.kind === "docker_compose" ? "Services" : "Container"}>
          {snapshot.containers.length === 0 ? (
            <p className="text-sm text-muted-foreground">No containers exist right now.</p>
          ) : (
            <>
              <ContainerTable containers={snapshot.containers} now={now} live={live} />
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
            </>
          )}
        </Panel>
      )}
      {unit && (
        <Panel title="Unit">
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="State" value={`${unit.active_state} (${unit.sub_state})`} />
            <Fact label="At boot" value={unit.enabled} />
            <Fact label="Active since" value={time(unit.active_since)} />
            {unitUp && <Fact label="Uptime" value={unitUp} />}
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
                {v.name}{" "}
                <span className="text-muted-foreground">
                  {v.driver}
                  {v.size != null ? ` · ${formatBytes(v.size)}` : ""}
                </span>
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
                {n.name}{" "}
                <span className="text-muted-foreground">
                  {[
                    n.driver,
                    n.scope,
                    n.internal ? "internal" : "",
                    n.subnets.join(", "),
                    n.gateways.length > 0 ? `gateway ${n.gateways.join(", ")}` : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
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
