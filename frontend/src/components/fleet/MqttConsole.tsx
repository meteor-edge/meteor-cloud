import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { listenMqttEvents, publishMqttTest, type MqttTestEvent } from "@/api/fleet";
import { ApiError } from "@/api/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn, formatDateTime } from "@/lib/utils";

type ConsoleMessage = {
  direction: "sent" | "received";
  topic: string;
  payload: string;
  at: string;
};

type MqttConsoleProps = {
  token: string;
  organizationId: string;
  defaultTopic?: string;
  topicLocked?: boolean;
  canPublish: boolean;
  topicPlaceholder?: string;
};

export function MqttConsole({
  token,
  organizationId,
  defaultTopic = "",
  topicLocked = false,
  canPublish,
  topicPlaceholder = "devices/<device-id>/events",
}: MqttConsoleProps) {
  const [topic, setTopic] = useState(defaultTopic);
  const [payload, setPayload] = useState("hello from console");
  const [listening, setListening] = useState(false);
  const [messages, setMessages] = useState<ConsoleMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const sentRef = useRef<ConsoleMessage[]>([]);

  useEffect(() => {
    stopListening();
    setMessages([]);
    sentRef.current = [];
    setTopic(defaultTopic);
  }, [defaultTopic]);

  useEffect(() => {
    return () => {
      stopRef.current?.();
    };
  }, []);

  function stopListening() {
    stopRef.current?.();
    stopRef.current = null;
    setListening(false);
  }

  function startListening() {
    const nextTopic = topic.trim();
    if (!nextTopic) {
      setError("Enter a topic to listen on.");
      return;
    }
    setError(null);
    stopListening();
    setMessages([]);
    sentRef.current = [];
    setListening(true);
    stopRef.current = listenMqttEvents(
      token,
      organizationId,
      nextTopic,
      (event) => {
        if (isEchoOfSent(event, sentRef.current)) {
          return;
        }
        setMessages((current) => [...current, toReceived(event)]);
      },
      (err) => {
        setError(err instanceof ApiError ? err.message : err.message);
        setListening(false);
        stopRef.current = null;
      },
    );
  }

  const publishMutation = useMutation({
    mutationFn: () =>
      publishMqttTest(token, organizationId, {
        topic: topic.trim(),
        payload,
      }),
    onSuccess: (result) => {
      setError(null);
      const sent: ConsoleMessage = {
        direction: "sent",
        topic: result.topic,
        payload: result.payload,
        at: new Date().toISOString(),
      };
      sentRef.current = [...sentRef.current, sent].slice(-20);
      setMessages((current) => [...current, sent]);
    },
    onError: (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "Could not publish MQTT message."),
  });

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="mqtt-topic">Topic</Label>
        <Input
          id="mqtt-topic"
          value={topic}
          onChange={(event) => {
            stopListening();
            setMessages([]);
            sentRef.current = [];
            setTopic(event.target.value);
          }}
          placeholder={topicPlaceholder}
          readOnly={topicLocked}
          disabled={topicLocked}
        />
      </div>
      {canPublish ? (
        <div className="space-y-2">
          <Label htmlFor="mqtt-payload">Payload</Label>
          <textarea
            id="mqtt-payload"
            className="min-h-24 w-full rounded-md border border-input bg-white px-3 py-2 font-mono text-sm"
            value={payload}
            onChange={(event) => setPayload(event.target.value)}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {listening ? (
          <Button type="button" variant="outline" onClick={stopListening}>
            Stop
          </Button>
        ) : (
          <Button type="button" onClick={startListening} disabled={!topic.trim()}>
            Listen
          </Button>
        )}
        {canPublish ? (
          <Button
            type="button"
            variant="secondary"
            onClick={() => publishMutation.mutate()}
            disabled={!topic.trim() || publishMutation.isPending}
          >
            {publishMutation.isPending ? "Publishing…" : "Publish"}
          </Button>
        ) : null}
      </div>
      {error ? <p className="text-sm text-red-700">{error}</p> : null}
      {listening ? <p className="text-sm text-muted-foreground">Listening on {topic.trim()}</p> : null}
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Messages</p>
        {messages.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No messages yet. Listen, then publish from this page or a device.
          </p>
        ) : (
          <ol className="mt-3 space-y-3">
            {messages.map((message, index) => (
              <li
                key={`${message.at}-${index}`}
                className={cn(
                  "rounded-md p-3",
                  message.direction === "sent"
                    ? "ml-8 border border-primary/30 bg-primary/10"
                    : "mr-8 border border-border bg-secondary",
                )}
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span
                    className={cn(
                      "text-xs font-semibold uppercase tracking-wide",
                      message.direction === "sent" ? "text-primary" : "text-muted-foreground",
                    )}
                  >
                    {message.direction === "sent" ? "Sent" : "Received"}
                  </span>
                  <span className="text-xs text-muted-foreground">{formatDateTime(message.at)}</span>
                </div>
                <p className="mt-1 font-mono text-xs text-muted-foreground">{message.topic}</p>
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-sm">{message.payload}</pre>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function toReceived(event: MqttTestEvent): ConsoleMessage {
  return {
    direction: "received",
    topic: event.topic,
    payload: event.payload,
    at: event.received_at,
  };
}

function isEchoOfSent(event: MqttTestEvent, sent: ConsoleMessage[]): boolean {
  const cutoff = Date.now() - 8000;
  return sent.some(
    (item) =>
      item.topic === event.topic &&
      item.payload === event.payload &&
      Date.parse(item.at) >= cutoff,
  );
}
