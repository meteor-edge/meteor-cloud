import { resolveApiBaseUrl } from "@/lib/apiBase";
import { apiRequest, ApiError } from "@/api/http";

export type MqttTestEvent = {
  organization_id: string | null;
  device_id: string | null;
  topic: string;
  payload: string;
  received_at: string;
};

export type MqttTestPublishResult = {
  topic: string;
  payload: string;
};

export function publishMqttTest(
  token: string,
  organizationId: string,
  body: { topic?: string; deviceId?: string; payload?: string },
): Promise<MqttTestPublishResult> {
  return apiRequest<MqttTestPublishResult>(`/api/v1/organizations/${organizationId}/mqtt/publish`, {
    method: "POST",
    token,
    body: {
      topic: body.topic,
      device_id: body.deviceId,
      payload: body.payload,
    },
  });
}

export function listenMqttEvents(
  token: string,
  organizationId: string,
  topic: string,
  onEvent: (event: MqttTestEvent) => void,
  onError?: (error: Error) => void,
): () => void {
  const controller = new AbortController();
  const params = new URLSearchParams({ topic });

  void (async () => {
    try {
      const response = await fetch(
        `${resolveApiBaseUrl()}/api/v1/organizations/${organizationId}/mqtt/events?${params.toString()}`,
        {
          headers: {
            Accept: "text/event-stream",
            Authorization: `Bearer ${token}`,
          },
          signal: controller.signal,
        },
      );
      if (!response.ok) {
        throw new ApiError(response.status, "request_failed", `Listen failed with status ${response.status}`);
      }
      if (!response.body) {
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          for (const line of part.split("\n")) {
            if (!line.startsWith("data: ")) {
              continue;
            }
            onEvent(JSON.parse(line.slice(6)) as MqttTestEvent);
          }
        }
      }
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      onError?.(error instanceof Error ? error : new Error("MQTT listen failed"));
    }
  })();

  return () => controller.abort();
}
