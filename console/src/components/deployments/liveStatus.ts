/** Deploy statuses that can still change, so pages showing them should poll. */
export const LIVE_STATUSES: ReadonlySet<string> = new Set(["in_progress", "paused"]);

/** Poll interval for a Deploy that is still open. Progress is heartbeat-bound, so 5s is enough. */
export const LIVE_POLL_MS = 5000;

/** Poll the deployments list only while a row on the page can still change. */
export function listRefetchInterval(items: readonly { status: string }[] | undefined): number | false {
  return items?.some((item) => LIVE_STATUSES.has(item.status)) ? LIVE_POLL_MS : false;
}
