"""``meteorcli`` command-line interface.

Subcommands
-----------
  config     Store the control-plane domain and API key.
  test       Check that the server is reachable and the API key is valid.
  register   Register this device with a registration token (path 1).
  request-token  Ask the API for a device token; wait briefly for admin approval.
  claim          Collect the device token after a later approval.
  run        Send periodic heartbeats.
  mqtt-test    Publish one TLS MQTT message (default: devices/{id}/status).
  mqtt-listen  Print inbound commands (default: devices/{id}/commands).
  status       Show persisted (non-secret) configuration.

Secrets are never printed by any command except when the user explicitly
passes them on the command line.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from edge_agent.client import AgentApiError, EdgeClient
from edge_agent.config import AgentConfig, load_config, save_config
from edge_agent.credentials import read_device_token, read_registration_token_file
from edge_agent.heartbeat import run_loop, send_heartbeat
from edge_agent.inventory import collect_inventory
from edge_agent.registration import register
from meteorcli import __version__
from meteorcli.config import (
    CliPaths,
    chown_config_dir,
    default_config_dir,
    derive_api_base,
    persist_connection,
    resolve_server_url,
)
from meteorcli.credentials import read_secret, remove_secret, write_secret

logger = logging.getLogger("meteorcli")

PROG = "meteorcli"

ENV_SERVER = "METEORCLI_SERVER"
ENV_DOMAIN = "METEORCLI_DOMAIN"
ENV_TOKEN = "METEORCLI_TOKEN"
ENV_API_KEY = "METEORCLI_API_KEY"
ENV_CONFIG_DIR = "METEORCLI_CONFIG_DIR"

# Wait at most this long while polling for approval (override with --wait).
DEFAULT_ENROLL_WAIT_SECONDS = 300
# Never poll faster than this, even if the server suggests a shorter interval.
MIN_POLL_INTERVAL_SECONDS = 10
# Extra delay after HTTP 429 before the next poll (seconds).
RATE_LIMIT_BACKOFF_SECONDS = 30


def _normalize_secret(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().strip("\"'").strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    return cleaned or None


def _paths_from_args(args: argparse.Namespace) -> CliPaths:
    config_dir = args.config_dir or os.environ.get(ENV_CONFIG_DIR)
    if config_dir:
        return CliPaths.from_dir(Path(config_dir).expanduser())
    return CliPaths.from_dir(default_config_dir())


def _finish_config_write(paths: CliPaths) -> None:
    chown_config_dir(paths.config_path.parent)
    if os.environ.get("SUDO_USER") and os.geteuid() == 0:
        print(
            f"note: sudo is not required; config is stored for {os.environ['SUDO_USER']} at {paths.config_path.parent}",
            file=sys.stderr,
        )


def _load(paths: CliPaths) -> AgentConfig | None:
    return load_config(paths.agent_paths())


def _resolve_server(args: argparse.Namespace, config: AgentConfig | None) -> str | None:
    override = getattr(args, "server", None) or os.environ.get(ENV_SERVER)
    return resolve_server_url(config, override=override)


def _resolve_token(args: argparse.Namespace) -> tuple[str | None, Path | None]:
    if args.token:
        return args.token, None
    if args.token_file:
        token_file = Path(args.token_file)
        return read_registration_token_file(token_file), token_file
    env_token = os.environ.get(ENV_TOKEN)
    if env_token:
        return env_token, None
    return None, None


def _resolve_api_key(args: argparse.Namespace, paths: CliPaths) -> str | None:
    flag = _normalize_secret(getattr(args, "api_key", None))
    if flag:
        return flag
    env_key = _normalize_secret(os.environ.get(ENV_API_KEY))
    if env_key:
        return env_key
    return _normalize_secret(read_secret(paths.api_key_path))


def _cmd_config(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    domain = args.domain or os.environ.get(ENV_DOMAIN)
    api_base = args.api_base
    api_key = _normalize_secret(args.api_key or os.environ.get(ENV_API_KEY))

    if args.show:
        config = _load(paths)
        server = resolve_server_url(config)
        has_key = read_secret(paths.api_key_path) is not None
        has_device = read_device_token(paths.token_path) is not None
        print(f"Domain:         {config.domain if config else '—'}")
        print(f"API base:       {server or '—'}")
        print(f"API key:        {'present' if has_key else 'MISSING'}")
        print(f"Device ID:      {config.device_id if config and config.device_id else '—'}")
        print(f"Credential:     {'present' if has_device else 'MISSING'}")
        return 0 if config is not None or has_key else 1

    if not domain and not api_base and not api_key:
        print(
            f"error: provide --domain, --api-base, and/or --api-key (or {ENV_DOMAIN}/{ENV_API_KEY}).",
            file=sys.stderr,
        )
        return 2

    persist_connection(paths, domain=domain, api_base=api_base, http=args.http)
    if api_key:
        write_secret(paths.api_key_path, api_key)
    _finish_config_write(paths)
    print(f"Configuration saved under {paths.config_path.parent}")
    print(f"Next: verify with '{PROG} test'.")
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    config = _load(paths)
    server = _resolve_server(args, config)
    if not server:
        print(
            f"error: a server URL is required (use --server, {ENV_SERVER}, or '{PROG} config --domain').",
            file=sys.stderr,
        )
        return 2

    try:
        token, token_file = _resolve_token(args)
    except FileNotFoundError:
        print(f"error: token file not found: {args.token_file}", file=sys.stderr)
        return 2
    if not token:
        print(
            f"error: a registration token is required (use --token, --token-file, or {ENV_TOKEN}).",
            file=sys.stderr,
        )
        return 2

    client = EdgeClient(server)
    try:
        result = register(
            client=client,
            paths=paths.agent_paths(),
            token=token,
            name=args.name,
            token_file=token_file,
        )
    except AgentApiError as error:
        logger.error("Registration failed: %s", error.code)
        print(f"error: registration failed: {error.message}", file=sys.stderr)
        return 1

    print(f"Registered device {result.device_id}")
    print(f"Organization:   {result.organization_id}")
    print(f"Name:           {result.name}")
    print(f"Heartbeat:      every {result.heartbeat_interval_seconds}s")
    print(f"Credential:     stored at {paths.token_path}")
    print()
    print(f"Next: start sending heartbeats with '{PROG} run'.")
    chown_config_dir(paths.config_path.parent)
    return 0


def _persist_claimed_device(
    paths: CliPaths,
    client: EdgeClient,
    poll: dict,
) -> None:
    from edge_agent.mqtt_config import mqtt_from_api_payload
    from edge_agent.persist import persist_device_secrets

    persist_device_secrets(
        paths.config_path.parent,
        paths.token_path,
        poll["device_token"],
        mqtt_from_api_payload(poll),
    )
    existing = _load(paths) or AgentConfig(server_url=client.server_url)
    existing.server_url = client.server_url
    existing.device_id = str(poll["device_id"])
    existing.organization_id = str(poll["organization_id"])
    existing.name = str(poll.get("name") or existing.name)
    existing.heartbeat_interval_seconds = int(poll.get("heartbeat_interval_seconds") or 60)
    save_config(paths.agent_paths(), existing)
    remove_secret(paths.claim_secret_path)
    chown_config_dir(paths.config_path.parent)


def _sync_stored_claim(paths: CliPaths, client: EdgeClient, pending: dict) -> str:
    """Check a stored claim against the API. Terminal states clear the local secret."""
    try:
        polled = client.enroll_poll(
            request_id=pending["request_id"],
            claim_secret=pending["claim_secret"],
        )
    except AgentApiError:
        return "pending"
    status = str(polled.get("status") or "pending")
    if status in {"rejected", "expired"}:
        remove_secret(paths.claim_secret_path)
        return status
    if status == "approved" and polled.get("device_token"):
        _persist_claimed_device(paths, client, polled)
        _print_claimed(paths, polled)
        return "approved"
    return "pending"


def _load_pending_claim(paths: CliPaths) -> dict | None:
    raw = read_secret(paths.claim_secret_path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    request_id = data.get("request_id")
    claim_secret = data.get("claim_secret")
    if not request_id or not claim_secret:
        return None
    return {"request_id": str(request_id), "claim_secret": str(claim_secret)}


def _poll_interval(polled: dict | None, fallback: int) -> int:
    suggested = fallback
    if polled is not None and polled.get("poll_interval_seconds") is not None:
        try:
            suggested = int(polled["poll_interval_seconds"])
        except (TypeError, ValueError):
            suggested = fallback
    return max(MIN_POLL_INTERVAL_SECONDS, suggested, 1)


def _print_claimed(paths: CliPaths, polled: dict) -> None:
    print(f"Registered device {polled['device_id']}")
    print(f"Name:           {polled.get('name')}")
    print(f"Credential:     stored at {paths.token_path}")
    print()
    print(f"Next: start sending heartbeats with '{PROG} run'.")


def _print_pending_hint(request_id: str) -> None:
    print(f"Enrollment request {request_id} is still waiting for approval.")
    print(f"The claim secret is saved. When an administrator approves it, run '{PROG} claim'.")


def _handle_poll_payload(paths: CliPaths, client: EdgeClient, polled: dict) -> int | None:
    """Return an exit code for a terminal poll, or None to keep waiting."""
    status = polled.get("status")
    if status == "pending":
        return None
    if status == "rejected":
        reason = polled.get("rejection_reason") or "no reason given"
        print(f"Enrollment rejected: {reason}", file=sys.stderr)
        remove_secret(paths.claim_secret_path)
        return 1
    if status == "expired":
        print("Enrollment request expired. Submit a new request.", file=sys.stderr)
        remove_secret(paths.claim_secret_path)
        return 1
    if status == "approved" and polled.get("device_token"):
        _persist_claimed_device(paths, client, polled)
        _print_claimed(paths, polled)
        return 0
    if status == "approved":
        print("Approved, but the device credential was already claimed.", file=sys.stderr)
        remove_secret(paths.claim_secret_path)
        return 1
    print(f"error: unexpected enrollment status: {status}", file=sys.stderr)
    return 1


def _poll_enrollment(
    paths: CliPaths,
    client: EdgeClient,
    *,
    request_id: str,
    claim_secret: str,
    wait_seconds: int,
    fallback_interval: int,
    sleep,
    max_polls: int | None,
) -> int:
    wait_seconds = max(wait_seconds, 0)
    deadline = time.monotonic() + wait_seconds
    polls = 0
    interval = max(MIN_POLL_INTERVAL_SECONDS, fallback_interval)
    poll_cap = max(1, wait_seconds // interval + 1)
    if max_polls is not None:
        poll_cap = min(poll_cap, max_polls)
    while True:
        try:
            polled = client.enroll_poll(request_id=request_id, claim_secret=claim_secret)
        except AgentApiError as error:
            if error.status == 429:
                polls += 1
                print(
                    f"Server asked to slow down; waiting {RATE_LIMIT_BACKOFF_SECONDS}s before the next poll…",
                    file=sys.stderr,
                )
                if wait_seconds == 0 or polls >= poll_cap or time.monotonic() >= deadline:
                    _print_pending_hint(request_id)
                    return 2
                sleep(RATE_LIMIT_BACKOFF_SECONDS)
                continue
            print(f"error: poll failed: {error.message}", file=sys.stderr)
            return 1

        outcome = _handle_poll_payload(paths, client, polled)
        if outcome is not None:
            return outcome

        polls += 1
        interval = _poll_interval(polled, interval)
        remaining = deadline - time.monotonic()
        if wait_seconds == 0 or polls >= poll_cap or remaining <= 0:
            _print_pending_hint(request_id)
            return 2
        sleep(min(interval, remaining))


def _cmd_request(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    config = _load(paths)
    server = _resolve_server(args, config)
    if not server:
        print(
            f"error: a server URL is required (use --server, {ENV_SERVER}, or '{PROG} config --domain').",
            file=sys.stderr,
        )
        return 2

    api_key = _resolve_api_key(args, paths)
    if not api_key:
        print(
            f"error: an API key is required (use --api-key, {ENV_API_KEY}, or '{PROG} config --api-key').",
            file=sys.stderr,
        )
        return 2

    client = EdgeClient(server)
    pending = _load_pending_claim(paths)
    if pending is not None and not args.new:
        status = _sync_stored_claim(paths, client, pending)
        if status == "approved":
            return 0
        if status == "pending":
            print(
                f"error: a pending enrollment request already exists ({pending['request_id']}).",
                file=sys.stderr,
            )
            print(
                f"Run '{PROG} claim' to collect the token after approval, "
                f"or '{PROG} request-token --new' to submit a different request.",
                file=sys.stderr,
            )
            return 2
        print(
            f"Previous enrollment request was {status}. Submitting a new request.",
            file=sys.stderr,
        )

    inventory = collect_inventory()
    try:
        submitted = client.enroll_request(api_key=api_key, inventory=inventory, name=args.name)
    except AgentApiError as error:
        print(f"error: enrollment request failed: {error.message}", file=sys.stderr)
        return 1

    request_id = str(submitted["request_id"])
    claim_secret = str(submitted["claim_secret"])
    write_secret(
        paths.claim_secret_path,
        json.dumps({"request_id": request_id, "claim_secret": claim_secret}),
    )
    interval = int(submitted.get("poll_interval_seconds") or MIN_POLL_INTERVAL_SECONDS)
    print(f"Enrollment request {request_id} submitted.")
    wait_seconds = max(0, int(args.wait))
    if wait_seconds == 0:
        print("Not waiting. After an administrator approves this device, collect the token with:")
        print(f"  {PROG} claim")
        return 0

    print(
        "Waiting for an administrator to approve this device "
        f"(at most {wait_seconds}s, polling every {max(MIN_POLL_INTERVAL_SECONDS, interval)}s)…"
    )
    return _poll_enrollment(
        paths,
        client,
        request_id=request_id,
        claim_secret=claim_secret,
        wait_seconds=wait_seconds,
        fallback_interval=interval,
        sleep=getattr(args, "_sleep", time.sleep),
        max_polls=getattr(args, "_max_polls", None),
    )


def _cmd_claim(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    config = _load(paths)
    server = _resolve_server(args, config)
    if not server:
        print(
            f"error: a server URL is required (use --server, {ENV_SERVER}, or '{PROG} config --domain').",
            file=sys.stderr,
        )
        return 2

    pending = _load_pending_claim(paths)
    if pending is None:
        print(
            f"error: no pending token request to claim. "
            f"Run '{PROG} request-token' first and keep this config directory.",
            file=sys.stderr,
        )
        return 2

    client = EdgeClient(server)
    wait_seconds = max(0, int(args.wait))
    if wait_seconds > 0:
        print(f"Checking enrollment request {pending['request_id']} (waiting up to {wait_seconds}s)…")
    return _poll_enrollment(
        paths,
        client,
        request_id=pending["request_id"],
        claim_secret=pending["claim_secret"],
        wait_seconds=wait_seconds,
        fallback_interval=MIN_POLL_INTERVAL_SECONDS,
        sleep=getattr(args, "_sleep", time.sleep),
        max_polls=getattr(args, "_max_polls", None),
    )


def _cmd_test(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    config = _load(paths)
    server = _resolve_server(args, config)
    if not server:
        print(
            f"error: a server URL is required (use --server, {ENV_SERVER}, or '{PROG} config --domain').",
            file=sys.stderr,
        )
        if os.geteuid() != 0 and Path("/etc/meteorcli/config.json").exists():
            print(
                "hint: config was previously saved with sudo under /etc/meteorcli. "
                f"Re-run '{PROG} config --domain … --api-key …' without sudo.",
                file=sys.stderr,
            )
        return 2

    if args.http:
        server = derive_api_base(server, http=True)

    client = EdgeClient(server)
    try:
        health = client.health()
    except AgentApiError as error:
        print(f"error: cannot reach {server}: {error.message}", file=sys.stderr)
        return 1

    print(f"API base:       {server}")
    print(f"Server:         {health.get('status', 'ok')}")

    api_key = _resolve_api_key(args, paths)
    if not api_key:
        print("API key:        MISSING")
        print(
            f"error: an API key is required (use --api-key, {ENV_API_KEY}, or '{PROG} config --api-key').",
            file=sys.stderr,
        )
        return 1

    try:
        checked = client.check_api_key(api_key=api_key)
    except AgentApiError as error:
        prefix = api_key[:12] if api_key.startswith("key_") else "key_…"
        print(f"API key:        rejected ({error.message})", file=sys.stderr)
        print(
            f"hint: {server} does not recognize {prefix}…. "
            "Create a new key in Fleet → API keys on that same server, "
            f"then run '{PROG} config --api-key key_…' without sudo.",
            file=sys.stderr,
        )
        return 1

    print("API key:        ok")
    print(f"Organization:   {checked.get('organization_name') or checked.get('organization_id')}")
    print(f"Key name:       {checked.get('key_name') or '—'}")
    prefix = checked.get("key_prefix")
    if prefix:
        print(f"Key prefix:     {prefix}…")
    return 0


def _load_or_fail(paths: CliPaths) -> tuple[AgentConfig, str] | None:
    config = _load(paths)
    if config is None or not config.device_id:
        print(
            f"error: this device is not registered. Run '{PROG} register' or '{PROG} request-token' first.",
            file=sys.stderr,
        )
        return None
    device_token = read_device_token(paths.token_path)
    if device_token is None:
        print(
            f"error: device credential is missing. Re-register with '{PROG} register' or '{PROG} request-token'.",
            file=sys.stderr,
        )
        return None
    return config, device_token


def _cmd_run(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    loaded = _load_or_fail(paths)
    if loaded is None:
        return 1
    config, device_token = loaded
    client = EdgeClient(config.server_url)

    if args.once:
        try:
            response = send_heartbeat(client, device_token)
        except AgentApiError as error:
            print(f"error: heartbeat failed: {error.message}", file=sys.stderr)
            return 1
        print(f"Heartbeat ok; status: {response.get('status', 'unknown')}")
        return 0

    interval = args.interval or config.heartbeat_interval_seconds
    mqtt_session = None
    from edge_agent.mqtt import DeviceMqttSession
    from edge_agent.mqtt_config import read_mqtt_config

    logger.info("Starting heartbeat loop every %ss", interval)
    try:
        try:
            mqtt_config = read_mqtt_config(paths.config_path.parent)
            if mqtt_config is not None and config.device_id:
                mqtt_session = DeviceMqttSession(
                    config.device_id, mqtt_config, server_url=config.server_url
                )
                mqtt_session.start()
        except Exception:
            logger.exception("MQTT failed to start; continuing with heartbeats")
        run_loop(
            client,
            device_token,
            interval_seconds=interval,
            sleep=time.sleep,
            should_continue=lambda: True,
        )
    except AgentApiError:
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        logger.info("Stopping heartbeat loop.")
    finally:
        if mqtt_session is not None:
            mqtt_session.stop()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    config = _load(paths)
    has_credential = read_device_token(paths.token_path) is not None
    has_key = read_secret(paths.api_key_path) is not None
    if config is None and not has_key:
        print(f"Not configured. Run '{PROG} config --domain … --api-key …' first.")
        return 1
    server = resolve_server_url(config)
    print(f"Domain:         {config.domain if config else '—'}")
    print(f"API base:       {server or '—'}")
    print(f"API key:        {'present' if has_key else 'MISSING'}")
    print(f"Device ID:      {config.device_id if config and config.device_id else '—'}")
    print(f"Organization:   {config.organization_id if config and config.organization_id else '—'}")
    print(f"Name:           {config.name if config and config.name else '—'}")
    print(f"Heartbeat:      every {config.heartbeat_interval_seconds if config else 60}s")
    print(f"Credential:     {'present' if has_credential else 'MISSING'}")
    from edge_agent.mqtt_config import read_mqtt_config

    mqtt_ok = read_mqtt_config(paths.config_path.parent) is not None
    print(f"MQTT:           {'configured' if mqtt_ok else 'not configured'}")
    pending = _load_pending_claim(paths)
    if pending is not None:
        print(f"Pending claim:  {pending['request_id']} (run '{PROG} claim')")
    return 0 if config and config.device_id else 1


def _mqtt_config_or_fail(paths, config):
    from edge_agent.mqtt_config import read_mqtt_config

    mqtt_config = read_mqtt_config(paths.config_path.parent)
    if mqtt_config is None or not config.device_id:
        print(
            "error: MQTT is not configured. Re-register or claim this device "
            "so mqtt.json is written next to the HTTP credential.",
            file=sys.stderr,
        )
        return None
    return mqtt_config


def _cmd_mqtt_test(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    loaded = _load_or_fail(paths)
    if loaded is None:
        return 1
    config, _device_token = loaded
    from datetime import UTC, datetime

    from edge_agent.mqtt import normalize_device_topic, publish_test_event

    mqtt_config = _mqtt_config_or_fail(paths, config)
    if mqtt_config is None:
        return 1
    try:
        topic = normalize_device_topic(config.device_id, args.topic, action="publish")
        if args.message is None:
            payload: dict | str = {
                "status": "online",
                "source": "meteorcli",
                "message": "mqtt-test",
                "sent_at": datetime.now(UTC).isoformat(),
            }
        else:
            raw = args.message.strip()
            if raw.startswith("{") or raw.startswith("["):
                payload = json.loads(raw)
            else:
                payload = args.message
    except (ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        published_topic = publish_test_event(
            config.device_id,
            mqtt_config,
            payload,
            topic=topic,
            server_url=config.server_url,
        )
    except Exception as error:
        logger.exception("MQTT test publish failed")
        print(f"error: MQTT test failed: {error}", file=sys.stderr)
        return 1
    print(f"Published MQTT test message to {published_topic}")
    return 0


def _cmd_mqtt_listen(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    loaded = _load_or_fail(paths)
    if loaded is None:
        return 1
    config, _device_token = loaded
    from edge_agent.mqtt import listen_mqtt, normalize_device_topic

    mqtt_config = _mqtt_config_or_fail(paths, config)
    if mqtt_config is None:
        return 1
    try:
        topic = normalize_device_topic(config.device_id, args.topic, action="subscribe")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Listening on {topic} (Ctrl+C to stop)", flush=True)

    def _print_message(message_topic: str, payload: str) -> None:
        print(f"{message_topic} {payload}", flush=True)

    try:
        listen_mqtt(
            config.device_id,
            mqtt_config,
            topic=topic,
            server_url=config.server_url,
            timeout=args.timeout,
            on_message=_print_message,
        )
    except KeyboardInterrupt:
        print("Stopped listening", file=sys.stderr)
        return 0
    except Exception as error:
        logger.exception("MQTT listen failed")
        print(f"error: MQTT listen failed: {error}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="MeteorCloud device agent.",
        epilog=(
            "Examples:\n"
            f"  {PROG} config --domain meteorxx.com --api-key key_XXXX\n"
            f"  {PROG} config --domain 192.168.0.107:8000 --api-key key_XXXX\n"
            f"  {PROG} test\n"
            f"  {PROG} request-token --name edge-01\n"
            f"  {PROG} claim\n"
            f"  {PROG} register --token reg_XXXX\n"
            f"  {PROG} run\n"
            f"  {PROG} mqtt-test\n"
            f"  {PROG} mqtt-test devices/DEVICE_ID/status '{{\"status\":\"online\"}}'\n"
            f"  {PROG} mqtt-listen\n"
            f"  {PROG} mqtt-listen commands\n"
            f"  {PROG} status\n"
            "\n"
            "Environment variables:\n"
            f"  {ENV_DOMAIN}       Server host or IP (used as-is; HTTP for IPs).\n"
            f"  {ENV_SERVER}       Override the API base URL (http:// is allowed).\n"
            f"  {ENV_API_KEY}      API key.\n"
            f"  {ENV_TOKEN}        Registration token (used if --token is omitted).\n"
            f"  {ENV_CONFIG_DIR}   Config/credential directory "
            f"(default: /etc/meteorcli as root, ~/.config/meteorcli otherwise).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"{PROG} {__version__}")
    parser.add_argument(
        "--config-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory for device config and credentials "
            "(default: /etc/meteorcli as root, ~/.config/meteorcli otherwise)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    config_parser = subparsers.add_parser(
        "config",
        help="Store the control-plane domain and API key.",
        description="Persist the server domain (and optional API key) used by later commands.",
    )
    config_parser.add_argument(
        "--domain",
        default=None,
        metavar="HOST",
        help=f"Server host or IP (meteorxx.com or 192.168.0.107:8000; or set {ENV_DOMAIN}).",
    )
    config_parser.add_argument(
        "--api-base",
        default=None,
        metavar="URL",
        help="Override the derived API origin (http:// is allowed for local testing).",
    )
    config_parser.add_argument(
        "--http",
        action="store_true",
        help="Use HTTP instead of HTTPS (for local testing).",
    )
    config_parser.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help=f"API key (or set {ENV_API_KEY}). Stored with 0600 permissions.",
    )
    config_parser.add_argument(
        "--show",
        action="store_true",
        help="Print the persisted configuration (never prints secrets).",
    )
    config_parser.set_defaults(func=_cmd_config)

    test_parser = subparsers.add_parser(
        "test",
        help="Check that the server is reachable and the API key is valid.",
        description=(
            "Reach the control plane and authenticate with the stored API key. Does not create an enrollment request."
        ),
    )
    test_parser.add_argument(
        "--server",
        default=None,
        metavar="URL",
        help=f"Control-plane base URL (or set {ENV_SERVER}).",
    )
    test_parser.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help=f"API key (or set {ENV_API_KEY} / stored key).",
    )
    test_parser.add_argument(
        "--http",
        action="store_true",
        help="Force HTTP when probing the server (for local testing).",
    )
    test_parser.set_defaults(func=_cmd_test)

    register_parser = subparsers.add_parser(
        "register",
        help="Register this device using a registration token.",
        description="Register this device with a one-time registration token.",
    )
    register_parser.add_argument(
        "--server",
        default=None,
        metavar="URL",
        help=f"Control-plane base URL (or set {ENV_SERVER}).",
    )
    register_parser.add_argument(
        "--token",
        default=None,
        metavar="TOKEN",
        help=f"Registration token value (or set {ENV_TOKEN}).",
    )
    register_parser.add_argument(
        "--token-file",
        default=None,
        metavar="PATH",
        help="Read the registration token from a file (recommended).",
    )
    register_parser.add_argument(
        "--name",
        default=None,
        metavar="NAME",
        help="Optional device name.",
    )
    register_parser.set_defaults(func=_cmd_register)

    request_parser = subparsers.add_parser(
        "request-token",
        aliases=["request"],
        help="Request a device token to connect this machine to the API.",
        description=(
            "Ask the control-plane API for a device token so this machine can connect. "
            "Uses the stored organization API key, then waits a limited time for an "
            "administrator to approve. If approval takes longer, run "
            f"'{PROG} claim' later. Does not wait forever and will not poll faster "
            f"than every {MIN_POLL_INTERVAL_SECONDS}s."
        ),
    )
    request_parser.add_argument(
        "--server",
        default=None,
        metavar="URL",
        help=f"Control-plane base URL (or set {ENV_SERVER}).",
    )
    request_parser.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help=f"API key (or set {ENV_API_KEY} / stored key).",
    )
    request_parser.add_argument(
        "--name",
        default=None,
        metavar="NAME",
        help="Optional requested device name.",
    )
    request_parser.add_argument(
        "--wait",
        type=int,
        default=DEFAULT_ENROLL_WAIT_SECONDS,
        metavar="SECONDS",
        help=(f"How long to poll for approval (default: {DEFAULT_ENROLL_WAIT_SECONDS}; 0 submits and exits)."),
    )
    request_parser.add_argument(
        "--new",
        action="store_true",
        help="Submit a new token request even if a pending claim secret is already stored.",
    )
    request_parser.set_defaults(func=_cmd_request)

    claim_parser = subparsers.add_parser(
        "claim",
        help="Collect the device token after a later approval.",
        description=(
            "Use the stored claim secret from a previous request-token run to "
            "collect the device token once an administrator has approved it."
        ),
    )
    claim_parser.add_argument(
        "--server",
        default=None,
        metavar="URL",
        help=f"Control-plane base URL (or set {ENV_SERVER}).",
    )
    claim_parser.add_argument(
        "--wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Poll this long for approval (default: 0, a single check).",
    )
    claim_parser.set_defaults(func=_cmd_claim)

    run_parser = subparsers.add_parser(
        "run",
        help="Send heartbeats to the control plane.",
        description="Send periodic heartbeats so the device reports as online.",
    )
    run_parser.add_argument("--once", action="store_true", help="Send a single heartbeat and exit.")
    run_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Override the heartbeat interval.",
    )
    run_parser.set_defaults(func=_cmd_run)

    mqtt_test_parser = subparsers.add_parser(
        "mqtt-test",
        help="Publish one MQTT test message over TLS.",
        description=(
            "Publish to an allowed device topic using mqtt.json. With no topic, uses "
            "devices/{device_id}/status. Allowed publish suffixes: status, metrics, "
            "commands/result. Optional message is JSON if it starts with { or [, "
            "otherwise raw text."
        ),
    )
    mqtt_test_parser.add_argument(
        "topic",
        nargs="?",
        default=None,
        help="Topic or suffix (default: status). Allowed: status, metrics, commands/result.",
    )
    mqtt_test_parser.add_argument(
        "message",
        nargs="?",
        default=None,
        help="Payload to publish (default: a small mqtt-test JSON object).",
    )
    mqtt_test_parser.set_defaults(func=_cmd_mqtt_test)

    mqtt_listen_parser = subparsers.add_parser(
        "mqtt-listen",
        help="Print MQTT messages on a device topic.",
        description=(
            "Subscribe to this device's command topic over TLS and print payloads. "
            "With no topic, uses devices/{device_id}/commands (the only device "
            "subscribe). Does not answer ping. Uses a unique MQTT client id so "
            "meteorcli run stays connected."
        ),
    )
    mqtt_listen_parser.add_argument(
        "topic",
        nargs="?",
        default=None,
        help="Topic or suffix (default: commands). Devices may only subscribe to commands.",
    )
    mqtt_listen_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop after this many seconds (default: until Ctrl+C).",
    )
    mqtt_listen_parser.set_defaults(func=_cmd_mqtt_listen)

    status_parser = subparsers.add_parser(
        "status",
        help="Show the persisted (non-secret) configuration.",
        description="Show persisted configuration. Never prints credentials.",
    )
    status_parser.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
