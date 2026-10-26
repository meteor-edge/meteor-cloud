import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Download, Loader2, RefreshCw, ScrollText, Search, Square } from "lucide-react";

import { createLogSession, getLogSources } from "@/api/fleet";
import type { LogSessionRequest, LogSource } from "@/api/fleet/types";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { websocketUrl } from "@/lib/apiBase";
import { groupSources, sourceKey, sourceLabel } from "@/lib/logSources";
import { formatRelativeTime } from "@/lib/utils";

type DeviceLogsProps = {
  token: string;
  organizationId: string;
  deviceId: string;
  deviceName?: string;
};

type Status = "idle" | "connecting" | "streaming" | "ended";
type Since = LogSessionRequest["since"];

const LINE_OPTIONS = [100, 500, 2000, 5000];
const SINCE_OPTIONS: { value: Since; label: string }[] = [
  { value: null, label: "Any time" },
  { value: "15m", label: "Last 15 min" },
  { value: "1h", label: "Last hour" },
  { value: "6h", label: "Last 6 hours" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
];

export function DeviceLogs({ token, organizationId, deviceId, deviceName }: DeviceLogsProps) {
  // Listed on the device over SSH, so only when this window is opened and on Refresh.
  // The token is left out of the key so a token refresh does not list again.
  const sourcesQuery = useQuery({
    queryKey: ["log-sources", organizationId, deviceId],
    queryFn: () => getLogSources(token, organizationId, deviceId),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: false,
  });

  const [selected, setSelected] = useState<LogSource | null>(null);
  const [sourceFilter, setSourceFilter] = useState("");
  const [lines, setLines] = useState(500);
  const [since, setSince] = useState<Since>(null);
  const [follow, setFollow] = useState(true);
  const [grepDraft, setGrepDraft] = useState("");
  const [grep, setGrep] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  // Bumped by every stop (and unmount), so a session that resolves late is dropped.
  const generationRef = useRef(0);

  // A read-only terminal: it keeps container colors, scrollback and scrolling for us.
  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    const term = new Terminal({
      convertEol: true,
      disableStdin: true,
      cursorStyle: "bar",
      cursorInactiveStyle: "none",
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: 12,
      scrollback: 20_000,
      theme: { background: "#000000", foreground: "#e5e5e5", selectionBackground: "#3f3f46" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(containerRef.current);
    termRef.current = term;
    return () => {
      observer.disconnect();
      generationRef.current += 1;
      socketRef.current?.close();
      term.dispose();
    };
  }, []);

  const stop = useCallback(() => {
    generationRef.current += 1;
    const socket = socketRef.current;
    socketRef.current = null;
    socket?.close();
    setStatus("ended");
  }, []);

  const start = useCallback(
    async (source: LogSource) => {
      stop();
      const generation = generationRef.current;
      termRef.current?.reset();
      setError(null);
      setNote(null);
      setStatus("connecting");
      try {
        const session = await createLogSession(token, organizationId, deviceId, {
          kind: source.kind,
          name: source.name,
          project: source.kind === "docker" ? (source.project ?? null) : null,
          lines,
          since,
          grep: grep.trim() || null,
          follow,
        });
        if (generation !== generationRef.current) {
          return;
        }
        const socket = new WebSocket(websocketUrl(session.websocket_path));
        // Only the current socket may touch the terminal or the status.
        const current = () => socketRef.current === socket;
        socket.binaryType = "arraybuffer";
        socket.onopen = () => {
          if (!current()) {
            return;
          }
          // The ticket goes in the first frame, not the URL, so it never reaches access logs.
          socket.send(JSON.stringify({ type: "auth", ticket: session.ticket }));
          setStatus("streaming");
        };
        socket.onmessage = (event) => {
          if (!current()) {
            return;
          }
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
          termRef.current?.write(new Uint8Array(event.data as ArrayBuffer));
        };
        socket.onerror = () => {
          if (current()) {
            setError("The connection to the device failed.");
          }
        };
        socket.onclose = () => {
          if (!current()) {
            return;
          }
          socketRef.current = null;
          setStatus("ended");
        };
        socketRef.current = socket;
      } catch (err) {
        if (generation !== generationRef.current) {
          return;
        }
        setError(err instanceof ApiError ? err.message : "Could not open the device's logs.");
        setStatus("idle");
      }
    },
    [deviceId, follow, grep, lines, organizationId, since, stop, token],
  );

  // Changing the source or a device-side option starts a new session.
  useEffect(() => {
    if (selected) {
      void start(selected);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, lines, since, follow, grep]);

  function download() {
    const buffer = termRef.current?.buffer.active;
    if (!buffer) {
      return;
    }
    const text = Array.from(
      { length: buffer.length },
      (_, i) => buffer.getLine(i)?.translateToString(true) ?? "",
    )
      .join("\n")
      .trimEnd();
    const url = URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain" }));
    const link = document.createElement("a");
    const name = selected ? sourceKey(selected).replace(/[^A-Za-z0-9._-]+/g, "_") : "logs";
    link.href = url;
    link.download = `${deviceName ?? deviceId}-${name}.log`;
    link.click();
    URL.revokeObjectURL(url);
  }

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
        <div className="flex gap-1">
          <Input
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            placeholder="Find a service, container, file…"
            aria-label="Find a log source"
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => void sourcesQuery.refetch()}
            disabled={sourcesQuery.isFetching}
            aria-label="Refresh sources"
            title="Refresh sources"
          >
            <RefreshCw className={`h-4 w-4 ${sourcesQuery.isFetching ? "animate-spin" : ""}`} />
          </Button>
        </div>
        <div className="max-h-[28rem] space-y-3 overflow-y-auto rounded-md border border-border p-2">
          {sourcesQuery.isLoading && (
            <p className="text-xs text-muted-foreground">Asking the device for its logs…</p>
          )}
          {data?.restricted && (
            <p className="text-xs text-muted-foreground">
              Showing logs of software deployed by MeteorCloud. System logs need terminal access.
            </p>
          )}
          {data && data.sources.length === 0 && (
            <p className="text-xs text-muted-foreground">No logs to show on this device.</p>
          )}
          {sourcesQuery.isError && (
            <p className="text-xs text-red-500">
              {sourcesQuery.error instanceof ApiError
                ? sourcesQuery.error.message
                : "Could not load log sources."}
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
        {data?.listed_at && (
          <p className="px-1 text-[11px] text-muted-foreground">
            Listed {formatRelativeTime(data.listed_at)}
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
              placeholder="Search"
              aria-label="Search"
              maxLength={200}
              className="h-8 w-44 text-xs"
            />
            <Button type="submit" size="sm" variant="outline" aria-label="Run search">
              <Search className="h-3.5 w-3.5" />
            </Button>
          </form>
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
              disabled={!selected}
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
            {grep.trim() && (
              <span className="shrink-0 text-zinc-500">matching “{grep.trim()}”</span>
            )}
          </div>
          <div className="relative h-[28rem]">
            <div ref={containerRef} className="absolute inset-0 p-2" data-testid="log-view" />

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
          </div>
        </div>
        {error && <p className="text-xs text-red-500">{error}</p>}
      </div>
    </div>
  );
}
