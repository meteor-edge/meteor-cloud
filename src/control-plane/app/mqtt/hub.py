"""In-process fan-out of MQTT payloads for the MQTT test UI."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from queue import Full, Queue
from threading import Lock
from typing import Any

from app.mqtt.topics import mqtt_topic_matches

_MAX_RECENT = 50
_MAX_QUEUE = 50


@dataclass(frozen=True)
class MqttTestEvent:
    topic: str
    payload: str
    received_at: str
    organization_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "device_id": str(self.device_id) if self.device_id else None,
            "topic": self.topic,
            "payload": self.payload,
            "received_at": self.received_at,
        }

    def sse_data(self) -> str:
        return json.dumps(self.as_dict())


class MqttEventHub:
    def __init__(self, *, max_recent: int = _MAX_RECENT, max_queue: int = _MAX_QUEUE) -> None:
        self._lock = Lock()
        self._max_recent = max_recent
        self._max_queue = max_queue
        self._recent: dict[tuple[uuid.UUID | None, str], deque[MqttTestEvent]] = defaultdict(
            lambda: deque(maxlen=self._max_recent)
        )
        self._subs: dict[tuple[uuid.UUID, str], set[Queue[MqttTestEvent]]] = defaultdict(set)

    def publish(self, event: MqttTestEvent) -> None:
        key = (event.organization_id, event.topic)
        with self._lock:
            self._recent[key].append(event)
            deliveries: list[Queue[MqttTestEvent]] = []
            for (org_id, topic_filter), queues in self._subs.items():
                if not mqtt_topic_matches(topic_filter, event.topic):
                    continue
                if event.organization_id != org_id:
                    continue
                deliveries.extend(queues)
        for queue in deliveries:
            self._offer(queue, event)

    def subscribe(self, organization_id: uuid.UUID, topic_filter: str) -> Queue[MqttTestEvent]:
        sub_key = (organization_id, topic_filter)
        queue: Queue[MqttTestEvent] = Queue(maxsize=self._max_queue)
        with self._lock:
            self._subs[sub_key].add(queue)
            replay = [
                event
                for (org_id, topic), events in self._recent.items()
                if mqtt_topic_matches(topic_filter, topic) and org_id == organization_id
                for event in events
            ]
        for event in replay:
            self._offer(queue, event)
        return queue

    def unsubscribe(
        self,
        organization_id: uuid.UUID,
        topic_filter: str,
        queue: Queue[MqttTestEvent],
    ) -> None:
        sub_key = (organization_id, topic_filter)
        with self._lock:
            listeners = self._subs.get(sub_key)
            if listeners is None:
                return
            listeners.discard(queue)
            if not listeners:
                self._subs.pop(sub_key, None)

    def _offer(self, queue: Queue[MqttTestEvent], event: MqttTestEvent) -> None:
        """Enqueue an event; drop it if the subscriber is not keeping up."""
        try:
            queue.put_nowait(event)
        except Full:
            pass


_hub = MqttEventHub()


def get_mqtt_event_hub() -> MqttEventHub:
    return _hub


def set_mqtt_event_hub(hub: MqttEventHub) -> None:
    global _hub
    _hub = hub
