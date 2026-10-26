import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, Download, Loader2, RefreshCw, ScrollText, Search, Square } from "lucide-react";

import { createLogSession, getLogSources } from "@/api/fleet";
import type { LogSessionRequest, LogSource } from "@/api/fleet/types";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { websocketUrl } from "@/lib/apiBase";
import { appendLines, buildMatcher, groupSources, sourceKey, sourceLabel } from "@/lib/logSources";
import { formatDateTime } from "@/lib/utils";

type DeviceLogsProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  deviceName?: string;
};

type Status = "idle" | "connecting" | "streaming" | "ended";
type Since = LogSessionRequest["since"];

const LINE_HEIGHT = 18;
const OVERSCAN = 20;
const LINE_OPTIONS = [100, 500, 2000, 5000];
const SINCE_OPTIONS: { value: Since; label: string }[] = [
  { value: null, label: "Any time" },
  { value: "15m", label: "Last 15 min" },
  { value: "1h", label: "Last hour" },
  { value: "6h", label: "Last 6 hours" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
];
function Highlighted({ line, matcher }: { line: string; matcher: RegExp | null }) {
  if (!matcher) {
    return <>{line}</>;
  }
  const parts: React.ReactNode[] = [];
  let last = 0;
  // matchAll starts at matcher.lastIndex, which filtering with .test() leaves moved.
  for (const match of line.matchAll(new RegExp(matcher.source, matcher.flags))) {
    if (!match[0]) {
      break;
    }
    const start = match.index ?? 0;
    parts.push(line.slice(last, start));
    parts.push(
      <mark key={start} className="rounded-sm bg-yellow-400/80 text-black">
        {match[0]}
      </mark>,
    );
    last = start + match[0].length;
  }
  parts.push(line.slice(last));
  return <>{parts}</>;
}

export function DeviceLogs({ token, organizationId, deviceId, deviceName }: DeviceLogsProps) {
  const sourcesQuery = useQuery({
    queryKey: ["log-sources", organizationId, deviceId, token],
    queryFn: () => getLogSources(token, organizationId, deviceId),
    refetchInterval: 60_000,
  });

  const [selected, setSelected] = useState<LogSource | null>(null);
  const [sourceFilter, setSourceFilter] = useState("");
  const [lines, setLines] = useState(500);
  const [since, setSince] = useState<Since>(null);
  const [follow, setFollow] = useState(true);
  const [grepDraft, setGrepDraft] = useState("");
  const [grep, setGrep] = useState("");
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [, setVersion] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewHeight, setViewHeight] = useState(448);

  const bufferRef = useRef<string[]>([]);
  const partialRef = useRef("");
  const socketRef = useRef<WebSocket | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);
  const frameRef = useRef<number | null>(null);

  const scheduleRender = useCallback(() => {
    if (frameRef.current !== null) {
      return;
    }
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      setVersion((v) => v + 1);
    });
  }, []);

  const append = useCallback(
    (text: string) => {
      partialRef.current = appendLines(bufferRef.current, partialRef.current, text);
      // At most one render per frame, however fast lines arrive.
      scheduleRender();
    },
    [scheduleRender],
  );

  const stop = useCallback(() => {
    const socket = socketRef.current;
    socketRef.current = null;
    socket?.close();
  }, []);

  const start = useCallback(
    async (source: LogSource) => {
      stop();
      bufferRef.current = [];
      partialRef.current = "";
      setError(null);
      setNote(null);
      setAutoScroll(true);
      setStatus("connecting");
      setVersion((v) => v + 1);
      try {
        const session = await createLogSession(token, organizationId, deviceId, {
          kind: source.kind,
          name: source.name,
          lines,
          since,
          grep: grep.trim() || null,
          follow,
        });
        const socket = new WebSocket(websocketUrl(session.websocket_path));
        const decoder = new TextDecoder();
        socket.binaryType = "arraybuffer";
        socket.onopen = () => {
          // The ticket goes in the first frame, not the URL, so it never reaches access logs.
          socket.send(JSON.stringify({ type: "auth", ticket: session.ticket }));
          setStatus("streaming");
        };
        socket.onmessage = (event) => {
          if (typeof event.data === "string") {
            // Text frames are status lines from the control plane, not log output.
            const message = event.data.trim();
            if (message.startsWith("error:")) {
              setError(message.slice("error:".length).trim());
            } else if (message) {
              setNote(message.replace(/^\[|\]$/g, ""));
            }
            return;
          }
          append(decoder.decode(new Uint8Array(event.data as ArrayBuffer), { stream: true }));
        };
        socket.onerror = () => setError("The connection to the device failed.");
        socket.onclose = () => {
          if (partialRef.current) {
            append("\n");
          }
          if (socketRef.current === socket) {
            socketRef.current = null;
          }
          setStatus("ended");
        };
        socketRef.current = socket;
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not open the device's logs.");
        setStatus("idle");
      }
    },
    [append, deviceId, follow, grep, lines, organizationId, since, stop, token],
  );

  // Changing the source or a device-side option starts a new session.
  useEffect(() => {
    if (selected) {
      void start(selected);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, lines, since, follow, grep]);

  useEffect(
    () => () => {
      stop();
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
      }
    },
    [stop],
  );

  useEffect(() => {
    const view = viewRef.current;
    if (!view) {
      return;
    }
    const observer = new ResizeObserver(() => setViewHeight(view.clientHeight || 448));
    observer.observe(view);
    return () => observer.disconnect();
  }, []);

  const matcher = useMemo(() => buildMatcher(filter), [filter]);
  const buffer = bufferRef.current;
  const visible = matcher
    ? buffer.filter((line) => {
        matcher.lastIndex = 0;
        return matcher.test(line);
      })
    : buffer;

  // Keep the newest line in view while following; scrolling up pauses that.
  useEffect(() => {
    const view = viewRef.current;
    if (view && autoScroll) {
      view.scrollTop = view.scrollHeight;
    }
  });

  function onScroll() {
    const view = viewRef.current;
    if (!view) {
      return;
    }
    setScrollTop(view.scrollTop);
    const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < LINE_HEIGHT * 2;
    setAutoScroll(atBottom);
  }

  function jumpToLatest() {
    setAutoScroll(true);
    const view = viewRef.current;
    if (view) {
      view.scrollTop = view.scrollHeight;
    }
  }

  function download() {
    const blob = new Blob([visible.join("\n") + "\n"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const name = selected ? sourceKey(selected).replace(/[^A-Za-z0-9._-]+/g, "_") : "logs";
    link.href = url;
    link.download = `${deviceName ?? deviceId}-${name}.log`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const first = Math.max(0, Math.floor(scrollTop / LINE_HEIGHT) - OVERSCAN);
  const count = Math.ceil(viewHeight / LINE_HEIGHT) + OVERSCAN * 2;
  const windowed = visible.slice(first, first + count);

  const data = sourcesQuery.data;
  const needle = sourceFilter.trim().toLowerCase();
  const groups = groupSources(
    (data?.sources ?? []).filter(
      (s) => !needle || `${sourceLabel(s)} ${s.project ?? ""}`.toLowerCase().includes(needle),
    ),
    data?.deployed ?? [],
  );

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
      <div className="space-y-2">
        <Input
          value={sourceFilter}
          onChange={(event) => setSourceFilter(event.target.value)}
          placeholder="Find a service, container, file…"
          aria-label="Find a log source"
        />
        <div className="max-h-[28rem] space-y-3 overflow-y-auto rounded-md border border-border p-2">
          {sourcesQuery.isLoading && (
            <p className="text-xs text-muted-foreground">Loading sources…</p>
          )}
          {sourcesQuery.isError && (
            <p className="text-xs text-red-500">
              {sourcesQuery.error instanceof ApiError
                ? sourcesQuery.error.message
                : "Could not load log sources."}
            </p>
          )}
          {data && !data.reported_at && (
            <p className="text-xs text-muted-foreground">
              This device has not reported any log sources. Update meteorcli and re-run{" "}
              <code>sudo ./installcli.sh</code> so it installs the <code>meteorcli-logs</code>{" "}
              helper.
            </p>
          )}
          {groups.map((group) => (
            <div key={group.title}>
              <p className="px-1 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {group.title}
              </p>
              <ul>
                {group.sources.map((source) => {
                  const key = sourceKey(source);
                  const active = selected !== null && sourceKey(selected) === key;
                  return (
                    <li key={`${group.title}-${key}`}>
                      <button
                        type="button"
                        onClick={() => setSelected(source)}
                        title={key}
                        className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left font-mono text-xs ${
                          active ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                        }`}
                      >
                        <span className="truncate">{sourceLabel(source)}</span>
                        {source.state && (
                          <span
                            className={`shrink-0 text-[10px] ${
                              active
                                ? ""
                                : ["running", "active"].includes(source.state)
                                  ? "text-green-600"
                                  : source.state === "failed"
                                    ? "text-red-500"
                                    : "text-muted-foreground"
                            }`}
                          >
                            {source.state}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
        {data?.reported_at && (
          <p className="text-[11px] text-muted-foreground">
            Sources reported {formatDateTime(data.reported_at)}
          </p>
        )}
      </div>

      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select
            value={lines}
            onChange={(event) => setLines(Number(event.target.value))}
            className="h-8 rounded-md border border-input bg-background px-2"
            aria-label="Lines"
          >
            {LINE_OPTIONS.map((value) => (
              <option key={value} value={value}>
                Last {value} lines
              </option>
            ))}
          </select>
          <select
            value={since ?? ""}
            onChange={(event) => setSince((event.target.value || null) as Since)}
            className="h-8 rounded-md border border-input bg-background px-2"
            aria-label="Since"
          >
            {SINCE_OPTIONS.map((option) => (
              <option key={option.label} value={option.value ?? ""}>
                {option.label}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={follow}
              onChange={(event) => setFollow(event.target.checked)}
            />
            Follow
          </label>
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              setGrep(grepDraft);
            }}
          >
            <Input
              value={grepDraft}
              onChange={(event) => setGrepDraft(event.target.value)}
              placeholder="Search on device"
              aria-label="Search on device"
              maxLength={200}
              className="h-8 w-44 text-xs"
            />
            <Button type="submit" size="sm" variant="outline" aria-label="Search on device">
              <Search className="h-3.5 w-3.5" />
            </Button>
          </form>
          <Input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter shown lines (text or /regex/)"
            aria-label="Filter lines"
            className="h-8 w-56 text-xs"
          />
          <div className="ml-auto flex items-center gap-1">
            {status === "streaming" || status === "connecting" ? (
              <Button type="button" size="sm" variant="outline" onClick={stop}>
                <Square className="mr-1 h-3.5 w-3.5" /> Stop
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!selected}
                onClick={() => selected && void start(selected)}
              >
                <RefreshCw className="mr-1 h-3.5 w-3.5" /> Reload
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!visible.length}
              onClick={download}
            >
              <Download className="mr-1 h-3.5 w-3.5" /> Download
            </Button>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-zinc-800 bg-black shadow-lg">
          <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-xs text-zinc-300">
            <div className="flex min-w-0 items-center gap-2">
              <span
                className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                  status === "streaming"
                    ? "bg-green-500"
                    : status === "connecting"
                      ? "bg-yellow-400"
                      : "bg-zinc-600"
                }`}
                aria-hidden
              />
              <span className="truncate">
                {selected ? sourceKey(selected) : (deviceName ?? "device")}
              </span>
              <span className="shrink-0 text-zinc-500">
                {status === "streaming"
                  ? follow
                    ? "following"
                    : "loading"
                  : status === "connecting"
                    ? "connecting…"
                    : status === "ended"
                      ? (note ?? "ended")
                      : "not connected"}
              </span>
            </div>
            <span className="shrink-0 text-zinc-500">
              {matcher ? `${visible.length} of ${buffer.length}` : buffer.length} lines
              {grep.trim() ? ` · matching “${grep.trim()}”` : ""}
            </span>
          </div>
          <div className="relative h-[28rem]">
            <div
              ref={viewRef}
              onScroll={onScroll}
              className="absolute inset-0 overflow-auto p-2 font-mono text-xs text-zinc-200"
              data-testid="log-view"
            >
              <div style={{ height: visible.length * LINE_HEIGHT, position: "relative" }}>
                <div style={{ position: "absolute", top: first * LINE_HEIGHT, left: 0, right: 0 }}>
                  {windowed.map((line, index) => (
                    <div
                      key={first + index}
                      className="whitespace-pre"
                      style={{ height: LINE_HEIGHT, lineHeight: `${LINE_HEIGHT}px` }}
                    >
                      <Highlighted line={line} matcher={matcher} />
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {!selected && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/85 text-center">
                <ScrollText className="h-10 w-10 text-zinc-500" aria-hidden />
                <p className="font-mono text-sm text-zinc-200">Pick a log source</p>
                <p className="max-w-sm text-xs text-zinc-500">
                  Read over the device&apos;s WireGuard tunnel with a read-only helper; nothing is
                  stored in MeteorCloud.
                </p>
              </div>
            )}
            {status === "connecting" && (
              <div className="absolute right-3 top-3 flex items-center gap-1 text-xs text-zinc-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Connecting…
              </div>
            )}
            {!autoScroll && visible.length > 0 && (
              <button
                type="button"
                onClick={jumpToLatest}
                className="absolute bottom-3 right-5 flex items-center gap-1 rounded-full bg-zinc-800 px-3 py-1 text-xs text-zinc-100 shadow hover:bg-zinc-700"
              >
                <ArrowDown className="h-3.5 w-3.5" /> Jump to latest
              </button>
            )}
          </div>
        </div>
        {error && <p className="text-xs text-red-500">{error}</p>}
      </div>
    </div>
  );
}
