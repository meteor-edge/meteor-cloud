import { cn, formatBytes, formatDuration } from "@/lib/utils";

export type UploadProgressState = {
  loaded: number;
  total: number;
  /** Null until enough samples exist for a stable estimate. */
  bytesPerSecond: number | null;
};

/**
 * Show bytes sent, transfer speed in bytes/second, and estimated seconds remaining.
 * Once a positive total is reached, show storage processing without estimating its duration.
 */
export function UploadProgress({
  fileName,
  state,
}: {
  fileName: string;
  state: UploadProgressState;
}) {
  const { loaded, total, bytesPerSecond } = state;
  const percent = total > 0 ? Math.min(100, Math.floor((loaded / total) * 100)) : 0;
  // After the last byte is sent the server still writes the file to object storage,
  // which can take a while for multi-gigabyte images.
  const saving = total > 0 && loaded >= total;
  const secondsLeft =
    bytesPerSecond && bytesPerSecond > 0 ? (total - loaded) / bytesPerSecond : null;

  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="truncate font-medium" title={fileName}>
          {saving ? "Saving to storage…" : `Uploading ${fileName}`}
        </span>
        <span className="shrink-0 font-semibold tabular-nums">{percent}%</span>
      </div>

      <div
        className="h-2.5 overflow-hidden rounded-full bg-secondary"
        role="progressbar"
        aria-label="Upload progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={saving ? "Saving to storage" : `${percent}%`}
      >
        <div
          className={cn(
            "h-full rounded-full bg-aurora transition-[width] duration-300",
            saving && "animate-pulse",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>

      <p className="flex flex-wrap justify-between gap-x-3 text-xs text-muted-foreground tabular-nums">
        <span>
          {formatBytes(loaded)} of {formatBytes(total)}
        </span>
        {saving ? (
          <span>Verifying checksum and storing the file. Keep this window open.</span>
        ) : (
          <span>
            {bytesPerSecond ? `${formatBytes(bytesPerSecond)}/s` : "Measuring speed…"}
            {secondsLeft !== null && ` · about ${formatDuration(secondsLeft)} left`}
          </span>
        )}
      </p>
    </div>
  );
}
