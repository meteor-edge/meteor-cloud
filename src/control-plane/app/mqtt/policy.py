"""Declarative MQTT topic policy (the broker security rule set).

Every broker adapter (Mosquitto plugin, EMQX HTTP authz) asks the control plane
through ``/internal/mqtt/authorize``; this module is the single place that
answers. Rules are data, so later rule sets (customer topics, app identities)
can be added without touching broker config.

Model:

* Default deny. A request is allowed only when a rule for that principal kind
  matches the action and the topic exactly.
* ``{device_id}`` in a rule pattern is replaced by the caller's own device id,
  so a device can never match another device's topics.
* Topic filters with wildcards, ``$``-topics (``$SYS``, ``$share``, ``$CONTROL``)
  and the platform control namespace are always denied to devices.
* Platform users are superusers and are not evaluated here (the broker skips
  ACL checks for them after authentication).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

Action = Literal["publish", "subscribe", "read"]

#: Platform-only control topics (for example, disconnect a revoked device).
CONTROL_TOPIC_PREFIX = "meteorcloud/control/"
KICK_TOPIC = f"{CONTROL_TOPIC_PREFIX}disconnect"

#: Reserved for future device events / rule engine input; denied until a rule opens it.
RESERVED_DEVICE_SUFFIXES = ("events",)


@dataclass(frozen=True)
class TopicRule:
    """Allow ``actions`` on ``pattern`` for a device principal."""

    name: str
    actions: frozenset[str]
    pattern: str
    allow_retain: bool = False


#: The default device rule set. Order does not matter: any matching rule allows.
DEVICE_RULES: tuple[TopicRule, ...] = (
    TopicRule(
        name="device-status",
        actions=frozenset({"publish"}),
        pattern="devices/{device_id}/status",
        allow_retain=True,  # online/offline status and last will are retained
    ),
    TopicRule(name="device-metrics", actions=frozenset({"publish"}), pattern="devices/{device_id}/metrics"),
    TopicRule(
        name="device-command-results",
        actions=frozenset({"publish"}),
        pattern="devices/{device_id}/commands/result",
    ),
    TopicRule(
        name="device-commands-inbox",
        actions=frozenset({"subscribe", "read"}),
        pattern="devices/{device_id}/commands",
    ),
)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: str | None = None
    reason: str = ""


def normalize_action(action: str) -> Action | None:
    """Map broker action names to policy actions.

    EMQX sends ``publish``/``subscribe``; the Mosquitto plugin also sends
    ``read`` (delivery of a message to a subscriber).
    """
    value = action.strip().lower()
    if value.startswith("pub") or value == "write":
        return "publish"
    if value.startswith("sub"):
        return "subscribe"
    if value in {"read", "deliver"}:
        return "read"
    return None


def _has_wildcard(topic: str) -> bool:
    return any(part in {"+", "#"} for part in topic.split("/"))


def evaluate_device(
    device_id: uuid.UUID,
    action: str,
    topic: str,
    *,
    retain: bool = False,
    rules: tuple[TopicRule, ...] = DEVICE_RULES,
) -> Decision:
    """Decide whether ``device_id`` may perform ``action`` on ``topic``."""
    direction = normalize_action(action)
    if direction is None:
        return Decision(False, reason="unknown_action")
    if not topic or "\x00" in topic or len(topic) > 512:
        return Decision(False, reason="invalid_topic")
    if topic.startswith("$"):
        return Decision(False, reason="system_topic")
    if topic.startswith(CONTROL_TOPIC_PREFIX):
        return Decision(False, reason="control_topic")
    if _has_wildcard(topic):
        return Decision(False, reason="wildcard")
    for rule in rules:
        if direction not in rule.actions:
            continue
        if topic != rule.pattern.format(device_id=device_id):
            continue
        if direction == "publish" and retain and not rule.allow_retain:
            return Decision(False, rule=rule.name, reason="retain_not_allowed")
        return Decision(True, rule=rule.name)
    return Decision(False, reason="no_matching_rule")


DEVICE_CLIENT_ID_PREFIX = "device-"


def device_client_id_allowed(device_id: uuid.UUID, client_id: str) -> bool:
    """A device may only use ``device-<id>`` or ``device-<id>-<suffix>`` as client id.

    This stops one device from taking over (and kicking off) another device's
    session by reusing its client id. meteorcli already uses these forms.
    """
    own = f"{DEVICE_CLIENT_ID_PREFIX}{device_id}"
    return client_id == own or (client_id.startswith(own + "-") and len(client_id) <= 128)
