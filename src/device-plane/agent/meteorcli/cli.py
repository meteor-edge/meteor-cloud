"""``meteorcli`` command-line interface.

Subcommands
-----------
  config     Store the control-plane domain and API key.
  test       Check that the server is reachable and the API key is valid.
  register   Register this device with a registration token (path 1).
  request-token  Ask the API for a device token; wait briefly for admin approval.
  claim          Collect the device token after a later approval.
  run        Foreground heartbeats, MQTT, WireGuard (refuses if meteorcli.service is already active).
             With a pending token request, it first waits for approval. Under
             meteorcli.service it also waits for setup/registration instead of exiting.
  mqtt-test    Publish one TLS MQTT message (default: devices/{id}/status).
  mqtt-listen  Print inbound commands (default: devices/{id}/commands).
  status       Show persisted (non-secret) configuration.
  logs         List or print device logs (journal, systemd, Docker, /var/log).

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

from edge_agent.actions import ActionSync
from edge_agent.capabilities import collect_capabilities
from edge_agent.client import AgentApiError, EdgeClient
from edge_agent.config import AgentConfig, load_config, save_config
from edge_agent.credentials import read_device_token, read_registration_token_file
from edge_agent.heartbeat import run_loop, send_heartbeat
from edge_agent.inventory import collect_inventory
from edge_agent.platform import combine_heartbeat_handlers
from edge_agent.registration import register
from edge_agent.service import NOT_INSTALLED, UNIT, duplicate_run_message, running_as_service, service_state
from edge_agent.software import reconcile_software
from edge_agent.wifi import WifiSync
from meteorcli import __version__
from meteorcli import config as cli_config
from meteorcli.config import (
    CliPaths,
    chown_config_dir,
    default_config_dir,
    derive_api_base,
    lan_host_missing_port,
    persist_connection,
    resolve_server_url,
)
from meteorcli.credentials import read_secret, remove_secret, write_secret
from meteorcli.meteorenv import consume_meteorenv, find_meteorenv_files, read_enrollment

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
# meteorcli.service checks the config dir this often while the device is not registered.
SERVICE_IDLE_CHECK_SECONDS = 10
# After a failed auto enrollment from meteorenv / saved key, wait this long before trying again.
SERVICE_AUTO_ENROLL_BACKOFF_SECONDS = 60


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
    if os.environ.get("SUDO_USER") and os.geteuid() == 0 and not _service_reads(paths):
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

    _config, dropped = persist_connection(paths, domain=domain, api_base=api_base, http=args.http)
    if api_key:
        write_secret(paths.api_key_path, api_key)
    _finish_config_write(paths)
    server = resolve_server_url(_config)
    print(f"Configuration saved under {paths.config_path.parent}")
    if server:
        print(f"API:            {server}")
    # Judge the URL that was saved: --api-base wins over --domain, so hint on that flag.
    if server and lan_host_missing_port(server):
        flag, value = ("--api-base", server) if api_base else ("--domain", domain or server)
        print(
            f"hint: laptop `make dev` listens on port 8000, not 80. "
            f"Use '{PROG} config {flag} {value.rstrip('/')}:8000 …'",
            file=sys.stderr,
        )
    if dropped:
        print(f"Cleared previous enrollment ({', '.join(dropped)}); register again on this server.")
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
    _print_run_next_step(paths)
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
    pending = {"request_id": str(request_id), "claim_secret": str(claim_secret)}
    if data.get("name"):
        pending["name"] = str(data["name"])
    return pending


def _store_pending_claim(paths: CliPaths, submitted: dict, name: str | None) -> dict:
    """Save the claim secret. The requested name is kept so an expired request can be resubmitted."""
    pending = {"request_id": str(submitted["request_id"]), "claim_secret": str(submitted["claim_secret"])}
    if name:
        pending["name"] = name
    write_secret(paths.claim_secret_path, json.dumps(pending))
    return pending


def _poll_interval(polled: dict | None, fallback: int) -> int:
    suggested = fallback
    if polled is not None and polled.get("poll_interval_seconds") is not None:
        try:
            suggested = int(polled["poll_interval_seconds"])
        except (TypeError, ValueError):
            suggested = fallback
    return max(MIN_POLL_INTERVAL_SECONDS, suggested, 1)


def _service_reads(paths: CliPaths) -> bool:
    """True when meteorcli.service uses this config dir (it always reads /etc/meteorcli)."""
    try:
        return paths.config_path.parent.resolve() == cli_config.SYSTEM_CONFIG_DIR.resolve()
    except OSError:
        return False


def _service_will_connect(paths: CliPaths) -> bool:
    """True when the running meteorcli.service picks up enrollment written to ``paths``."""
    return _service_reads(paths) and service_state() == "active"


def _service_collects_claim(paths: CliPaths, server: str) -> bool:
    """True when the running service will poll a token request submitted to ``server``.

    Only a service that is still waiting for enrollment looks at new claims, and it
    polls the server saved in the config, so a ``--server`` override is not handed over.
    """
    if not _service_will_connect(paths) or _enrolled(paths):
        return False
    saved = resolve_server_url(_load(paths))
    return bool(saved) and saved.rstrip("/") == server.rstrip("/")


def _print_run_next_step(paths: CliPaths) -> None:
    state = service_state()
    if state == "active" and _service_reads(paths):
        print(f"{UNIT} is running and connects (heartbeats, MQTT, WireGuard) within a few seconds.")
        print("Follow logs with: journalctl -u meteorcli -f")
        return
    if state == "active":
        print(f"note: {UNIT} reads {cli_config.SYSTEM_CONFIG_DIR}, not {paths.config_path.parent}.")
        print(f"Next: {PROG} run    (foreground) or enroll again with sudo for the service")
        return
    if state != NOT_INSTALLED:
        print("Next: sudo systemctl enable --now meteorcli    (background)")
        print(f"  or: {PROG} run                              (foreground, Ctrl+C to stop)")
        return
    print(f"Next: {PROG} run    (foreground; Ctrl+C to stop)")
    print("      For a background agent, reinstall as root: sudo ./installcli.sh")


def _print_claimed(paths: CliPaths, polled: dict) -> None:
    print(f"Registered device {polled['device_id']}")
    print(f"Name:           {polled.get('name')}")
    print(f"Credential:     stored at {paths.token_path}")
    print()
    _print_run_next_step(paths)


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
    if status == "approved" and _enrolled(paths):
        # meteorcli.service (or another claimer) collected the token first.
        print("Approved. The device credential was collected by meteorcli.service.")
        remove_secret(paths.claim_secret_path)
        _print_run_next_step(paths)
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
            if _service_collects_claim(paths, server):
                print(
                    f"{UNIT} is waiting for its approval and connects afterwards. "
                    f"Use '{PROG} request-token --new' to submit a different request.",
                    file=sys.stderr,
                )
            else:
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
        return _request_failure_exit_code(error)

    stored = _store_pending_claim(paths, submitted, args.name)
    request_id = stored["request_id"]
    claim_secret = stored["claim_secret"]
    interval = int(submitted.get("poll_interval_seconds") or MIN_POLL_INTERVAL_SECONDS)
    print(f"Enrollment request {request_id} submitted.")
    service_waits = _service_collects_claim(paths, server)
    if args.wait is None:
        # The running service already polls this request; a second poller would only race it.
        wait_seconds = 0 if service_waits else DEFAULT_ENROLL_WAIT_SECONDS
    else:
        wait_seconds = max(0, int(args.wait))
    if wait_seconds == 0:
        if service_waits:
            print(f"{UNIT} is waiting for an administrator to approve this device and then connects.")
            print("Follow it with: journalctl -u meteorcli -f")
        else:
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


# request-token exit codes for scripts (first boot): retry later, or stop.
EXIT_KEY_REJECTED = 3  # wrong, revoked or expired key: retrying cannot help
EXIT_RETRY_LATER = 75  # EX_TEMPFAIL: network down, server busy or rate limited


def _request_failure_exit_code(error: AgentApiError) -> int:
    """Map a failed enrollment request to an exit code a first-boot script can act on."""
    if error.status in (401, 403):
        return EXIT_KEY_REJECTED
    if error.status == 0 or error.status == 429 or error.status >= 500:
        if error.retry_after:
            # Scripts read this line to wait as long as the server asked.
            print(f"retry-after: {error.retry_after}", file=sys.stderr)
        return EXIT_RETRY_LATER
    return 1


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
        if error.status == 404 and lan_host_missing_port(server):
            print(
                f"hint: that URL is port 80. Laptop `make dev` is :8000 — "
                f"re-run '{PROG} config --domain <ip>:8000 --api-key …'",
                file=sys.stderr,
            )
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


def _enrolled(paths: CliPaths) -> bool:
    """True when this config dir holds a device id and a device credential."""
    config = _load(paths)
    return bool(config and config.device_id) and read_device_token(paths.token_path) is not None


def _apply_meteorenv_enrollment(paths: CliPaths) -> str | None:
    """Load domain / API key / name from a boot meteorenv file into ``paths``.

    Returns the optional device name from the file, or None when nothing usable was found.
    """
    files = find_meteorenv_files()
    if not files:
        return None
    path = files[0]
    enrollment = read_enrollment(path)
    domain = (enrollment.get("METEOR_DOMAIN") or "").strip()
    api_base = (enrollment.get("METEOR_API_BASE") or "").strip()
    api_key = _normalize_secret(enrollment.get("METEOR_API_KEY"))
    name = (enrollment.get("METEOR_NAME") or "").strip() or None
    if not domain and not api_base and not api_key:
        return None
    if domain or api_base:
        persist_connection(paths, domain=domain or None, api_base=api_base or None)
    if api_key:
        write_secret(paths.api_key_path, api_key)
    _finish_config_write(paths)
    consume_meteorenv(path)
    logger.info(
        "Read enrollment settings from %s. Approve this device in MeteorCloud when the request appears.",
        path,
    )
    return name


def _submit_enrollment_request(paths: CliPaths, *, name: str | None) -> bool:
    """Submit one enrollment request using the saved server and API key. True when a claim is stored."""
    server = resolve_server_url(_load(paths))
    api_key = _normalize_secret(read_secret(paths.api_key_path))
    if not server or not api_key:
        return False
    if _load_pending_claim(paths) is not None:
        return True
    client = EdgeClient(server)
    try:
        submitted = client.enroll_request(api_key=api_key, inventory=collect_inventory(), name=name)
    except AgentApiError as error:
        logger.warning("Enrollment request failed (%s).", error.message)
        return False
    stored = _store_pending_claim(paths, submitted, name)
    logger.info(
        "Enrollment request %s submitted; waiting for approval in MeteorCloud.",
        stored["request_id"],
    )
    return True


def _service_wait_until_enrolled(paths: CliPaths, *, sleep) -> None:
    """meteorcli.service: idle until this device is registered, then return.

    The installer starts the service before anyone has run ``config`` or
    ``request-token``. Exiting here would make systemd restart it every few
    seconds, so it checks the config dir instead: a pending token request is
    waited on (and collected), a registration token written by ``register`` is
    picked up on the next check. A rejected or expired request goes back to idle.

    If a ``meteorenv.txt`` (or ``.meteorenv``) is on the boot partition, the service
    reads the enrollment fields, saves them under ``/etc/meteorcli``, submits
    ``request-token`` itself, and removes the file.
    """
    announced: str | None = None
    next_auto_enroll_at = 0.0
    while not _enrolled(paths):
        if _load_pending_claim(paths) is not None:
            announced = None
            _wait_for_pending_enrollment(paths, sleep=sleep)
            if _enrolled(paths):
                break
            # The request ended without a credential (rejected, refused key, no server,
            # or a token whose config write was interrupted): back to waiting.
            next_auto_enroll_at = 0.0
        else:
            now = time.monotonic()
            name: str | None = None
            if now >= next_auto_enroll_at:
                name = _apply_meteorenv_enrollment(paths)
                server = resolve_server_url(_load(paths))
                api_key = _normalize_secret(read_secret(paths.api_key_path))
                if server and api_key:
                    if _submit_enrollment_request(paths, name=name):
                        announced = None
                        continue
                    next_auto_enroll_at = now + SERVICE_AUTO_ENROLL_BACKOFF_SECONDS
            if not resolve_server_url(_load(paths)):
                if announced != "unconfigured":
                    logger.info(
                        "Waiting for setup. Put meteorenv.txt on the boot partition, or run "
                        "'sudo %s config --domain <host> --api-key key_…' and "
                        "'sudo %s request-token'.",
                        PROG,
                        PROG,
                    )
                    announced = "unconfigured"
            elif announced != "unregistered":
                logger.info(
                    "Waiting for registration. Put meteorenv.txt on the boot partition, or run "
                    "'sudo %s request-token' (or 'sudo %s register'); "
                    "this service connects as soon as the device is approved.",
                    PROG,
                    PROG,
                )
                announced = "unregistered"
        sleep(SERVICE_IDLE_CHECK_SECONDS)


def _claim_replaced(paths: CliPaths, pending: dict) -> bool:
    """True when the claim on disk is no longer ``pending`` (e.g. ``request-token --new`` ran)."""
    on_disk = _load_pending_claim(paths)
    return on_disk is not None and on_disk["request_id"] != pending["request_id"]


# Statuses from the enrollment poll that mean the request itself is unknown or revoked.
_ENROLL_INVALID_STATUSES = frozenset({401, 403, 404})


def _wait_for_pending_enrollment(paths: CliPaths, *, sleep) -> int | None:
    """Wait for an administrator to approve a token request submitted earlier.

    First boot (Meteor Imager's firstrun) runs ``request-token --wait 0``, deletes
    itself and starts meteorcli.service, so the wait for approval lives here and
    survives reboots. An expired request is resubmitted with the stored API key.

    Returns None when there is nothing to wait for or the device is now enrolled,
    or an exit code when the request ended without a device credential.
    """
    if _enrolled(paths):
        return None
    pending = _load_pending_claim(paths)
    if pending is None:
        return None
    server = resolve_server_url(_load(paths))
    if not server:
        print(f"error: no server configured. Run '{PROG} config --domain …' first.", file=sys.stderr)
        return 2
    client = EdgeClient(server)
    interval = MIN_POLL_INTERVAL_SECONDS
    logger.info(
        "Enrollment request %s is waiting for approval in MeteorCloud; checking every %ss.",
        pending["request_id"],
        interval,
    )
    while True:
        # Another process (an interactive `request-token`/`claim`, or `request-token --new`)
        # may have collected the token or replaced the request since the last poll.
        if _enrolled(paths):
            return None
        on_disk = _load_pending_claim(paths)
        if on_disk is None:
            return None
        if on_disk["request_id"] != pending["request_id"]:
            pending = on_disk
        try:
            polled = client.enroll_poll(request_id=pending["request_id"], claim_secret=pending["claim_secret"])
        except AgentApiError as error:
            if _claim_replaced(paths, pending):
                continue  # this answer is about the old request; poll the new one
            if error.status in _ENROLL_INVALID_STATUSES:
                print(
                    f"error: {server} does not know enrollment request {pending['request_id']} "
                    f"({error.message}). Submit a new one with '{PROG} request-token'.",
                    file=sys.stderr,
                )
                remove_secret(paths.claim_secret_path)
                return 1
            delay = RATE_LIMIT_BACKOFF_SECONDS if error.status == 429 else max(interval, RATE_LIMIT_BACKOFF_SECONDS)
            logger.warning("Could not check enrollment (%s); retrying in %ss.", error.message, delay)
            sleep(delay)
            continue

        status = polled.get("status")
        if status == "pending":
            interval = _poll_interval(polled, interval)
            sleep(interval)
            continue
        if status == "approved" and polled.get("device_token"):
            # Collect even if the claim was replaced meanwhile: the token is shown only once.
            _persist_claimed_device(paths, client, polled)
            logger.info("Approved: registered device %s (%s).", polled["device_id"], polled.get("name") or "-")
            return None
        if _claim_replaced(paths, pending):
            continue  # do not resubmit or drop a request someone already replaced
        if status == "expired":
            renewed = _resubmit_expired_request(paths, client, pending)
            if renewed is None:
                return 1
            if renewed is pending:
                sleep(RATE_LIMIT_BACKOFF_SECONDS)
            pending = renewed
            continue
        if status == "approved" and _enrolled(paths):
            # The token was collected by another process between our checks.
            return None
        return _handle_poll_payload(paths, client, polled)


def _resubmit_expired_request(paths: CliPaths, client: EdgeClient, pending: dict) -> dict | None:
    """Replace an expired request. Returns the new claim, ``pending`` to retry later, or None to stop."""
    api_key = _normalize_secret(read_secret(paths.api_key_path))
    if not api_key:
        print(
            f"error: enrollment request {pending['request_id']} expired and no API key is stored to "
            f"submit a new one. Run '{PROG} config --api-key key_…' and '{PROG} request-token'.",
            file=sys.stderr,
        )
        remove_secret(paths.claim_secret_path)
        return None
    try:
        submitted = client.enroll_request(
            api_key=api_key,
            inventory=collect_inventory(),
            name=pending.get("name"),
        )
    except AgentApiError as error:
        if error.status in _ENROLL_INVALID_STATUSES:
            print(f"error: enrollment request expired and the API key was refused: {error.message}", file=sys.stderr)
            remove_secret(paths.claim_secret_path)
            return None
        logger.warning("Enrollment request expired; resubmitting failed (%s), will retry.", error.message)
        return pending
    renewed = _store_pending_claim(paths, submitted, pending.get("name"))
    logger.info(
        "Enrollment request %s expired; submitted %s. Approve it in MeteorCloud.",
        pending["request_id"],
        renewed["request_id"],
    )
    return renewed


# meteorcli.service: wait between attempts after the server refused the device
# credential or something unexpected failed; doubles up to the maximum.
SERVICE_RETRY_MIN_SECONDS = 30
SERVICE_RETRY_MAX_SECONDS = 900


def _cmd_run(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    if args.once or not running_as_service():
        return _run_agent(paths, args)
    return _run_service(paths, args)


def _run_service(paths: CliPaths, args: argparse.Namespace) -> int:
    """meteorcli.service never gives up: a refused credential or an unexpected error is
    logged and tried again, slowly, instead of exiting into a systemd restart loop.

    Each attempt reads the config dir again, so enrolling the device again (or fixing
    a damaged file) is picked up without restarting the service.
    """
    delay = SERVICE_RETRY_MIN_SECONDS
    while True:
        started = time.monotonic()
        try:
            code = _run_agent(paths, args)
        except Exception as error:
            code, failure = None, error
        if code == 0:
            return 0
        if time.monotonic() - started >= SERVICE_RETRY_MAX_SECONDS:
            delay = SERVICE_RETRY_MIN_SECONDS  # it ran for a while: start the backoff over
        if code is None:
            logger.error("Unexpected error. The agent stays up and tries again in %ss.", delay, exc_info=failure)
        else:
            logger.error(
                "The agent could not connect with this device's credential (deleted or revoked in "
                "MeteorCloud?). It tries again in %ss. To enroll the device again: "
                "'sudo %s request-token --new' (or 'sudo %s register').",
                delay,
                PROG,
                PROG,
            )
        getattr(args, "_sleep", time.sleep)(delay)
        delay = min(delay * 2, SERVICE_RETRY_MAX_SECONDS)


def _run_agent(paths: CliPaths, args: argparse.Namespace) -> int:
    if not args.once:
        clash = duplicate_run_message(service_state())
        if clash:
            print(clash, file=sys.stderr)
            return 1
        wait_sleep = getattr(args, "_sleep", time.sleep)
        if running_as_service():
            _service_wait_until_enrolled(paths, sleep=wait_sleep)
        else:
            waited = _wait_for_pending_enrollment(paths, sleep=wait_sleep)
            if waited is not None:
                return waited
    loaded = _load_or_fail(paths)
    if loaded is None:
        return 1
    config, device_token = loaded
    chown_config_dir(paths.config_path.parent)
    client = EdgeClient(config.server_url)
    artifacts_dir = paths.config_path.parent / "artifacts"

    from edge_agent.mqtt_sync import MqttSupervisor
    from edge_agent.wireguard import ConnectivityState, describe_tunnel, sync_connectivity

    # One schedule per run loop; --once registers every time.
    wg_state = None if args.once else ConnectivityState()
    mqtt = (
        MqttSupervisor(config.device_id, paths.config_path.parent, server_url=config.server_url)
        if config.device_id and not args.once
        else None
    )

    last_tunnel = {"summary": ""}

    def _payload() -> dict:
        sync_connectivity(client, device_token, paths.config_path.parent, state=wg_state)
        # Log the tunnel only when it changes; `meteorcli status` shows it on demand.
        tunnel = describe_tunnel(paths.config_path.parent).summary()
        if tunnel != last_tunnel["summary"]:
            logger.info("%s", tunnel)
            last_tunnel["summary"] = tunnel
        if mqtt is not None:
            try:
                mqtt.sync(client, device_token)
            except Exception:
                logger.exception("MQTT sync failed; continuing with heartbeats")
        payload: dict = {**collect_inventory(), "capabilities": collect_capabilities()}
        reports = reconcile_software(client, device_token, artifacts_dir)
        # Under sudo, files this pass downloaded are root-owned; hand them back so a later
        # unprivileged run (and the meteorcli-systemd helper's owner check) can use them.
        try:
            chown_config_dir(paths.config_path.parent)
        except OSError:
            logger.warning("Could not give %s back to the sudo user", paths.config_path.parent)
        if reports:
            payload["software_report"] = reports
        return payload

    if args.once:
        try:
            response = send_heartbeat(client, device_token, _payload())
        except AgentApiError as error:
            print(f"error: heartbeat failed: {error.message}", file=sys.stderr)
            return 1
        print(f"Heartbeat ok; status: {response.get('status', 'unknown')}")
        print(describe_tunnel(paths.config_path.parent).summary())
        return 0

    interval = max(1, args.interval or config.heartbeat_interval_seconds)
    if running_as_service():
        logger.info("Running as %s", UNIT)
    else:
        logger.info("Running in the foreground (Ctrl+C to stop). This is not a background daemon.")
        if service_state() not in (NOT_INSTALLED, "active"):
            logger.info("Background: sudo systemctl enable --now meteorcli")
    logger.info("Heartbeat every %ss", interval)
    try:
        # MQTT starts (and later restarts with refreshed settings) from _payload on each heartbeat.
        run_loop(
            client,
            device_token,
            interval_seconds=interval,
            sleep=time.sleep,
            should_continue=lambda: True,
            payload_factory=_payload,
            on_response=combine_heartbeat_handlers(
                WifiSync(client, device_token).on_heartbeat,
                ActionSync(client, device_token, config_dir=paths.config_path.parent).on_heartbeat,
            ),
        )
    except AgentApiError:
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        logger.info("Stopping heartbeat loop.")
    finally:
        if mqtt is not None:
            mqtt.stop()
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
    state = service_state()
    print(f"Service:        {UNIT} is {state}")
    from edge_agent.mqtt_config import read_mqtt_config
    from edge_agent.wireguard import describe_tunnel

    mqtt_ok = read_mqtt_config(paths.config_path.parent) is not None
    print(f"MQTT:           {'configured' if mqtt_ok else 'not configured'}")
    print(f"WireGuard:      {describe_tunnel(paths.config_path.parent).summary()}")
    pending = _load_pending_claim(paths)
    if pending is not None:
        print(f"Pending claim:  {pending['request_id']} (run '{PROG} claim')")
    return 0 if config and config.device_id else 1


def _source_spec(item: dict) -> str:
    return item["kind"] if item["kind"] == "journal" else f"{item['kind']}:{item['name']}"


def _cmd_logs(args: argparse.Namespace) -> int:
    import subprocess

    from edge_agent.logs import LOGS_HELPER, helper_command, helper_installed, list_log_sources

    if not helper_installed():
        print(f"error: {LOGS_HELPER} is not installed. Re-run: sudo ./installcli.sh", file=sys.stderr)
        return 1
    if args.source is None:
        sources = list_log_sources()
        if sources is None:
            print(
                f"error: {LOGS_HELPER} list failed (passwordless sudo for it is set up by installcli.sh).",
                file=sys.stderr,
            )
            return 1
        if args.json:
            print(json.dumps(sources, indent=2))
            return 0
        for item in sources:
            detail = " ".join(str(item[key]) for key in ("state", "project") if key in item)
            print(f"{_source_spec(item):<60} {detail}".rstrip())
        return 0
    kind, _, name = args.source.partition(":")
    argv = helper_command("show", kind, *([name] if name else []), "--lines", str(args.lines))
    if args.since:
        argv += ["--since", args.since]
    if args.grep:
        argv += ["--grep", args.grep]
    if args.follow:
        argv.append("--follow")
    try:
        return subprocess.run(argv, check=False).returncode
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return 130


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
            f'  {PROG} mqtt-test devices/DEVICE_ID/status \'{{"status":"online"}}\'\n'
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
        default=None,
        metavar="SECONDS",
        help=(
            f"How long to poll for approval (default: {DEFAULT_ENROLL_WAIT_SECONDS}, or 0 when "
            "meteorcli.service is running and waits instead; 0 submits and exits)."
        ),
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
        description=(
            "Foreground loop: heartbeats, MQTT, and WireGuard. Stays in this terminal "
            "(Ctrl+C to stop). If meteorcli.service is already active, this command "
            "exits so you do not run two agents. --once still runs one pass. "
            "If a token request is still pending, it first waits for approval "
            "(waits for pending enrollment), resubmitting an expired request with the stored API key. "
            "As meteorcli.service it also waits until the device is configured and registered."
        ),
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

    logs_parser = subparsers.add_parser(
        "logs",
        help="List or print this device's logs (journal, systemd, Docker, /var/log).",
        description=(
            "Without SOURCE, list the log sources the console's Logs window offers. "
            "SOURCE is journal, systemd:UNIT, docker:CONTAINER or file:PATH. "
            "Uses the root-owned meteorcli-logs helper from installcli.sh."
        ),
    )
    logs_parser.add_argument("source", nargs="?", help="journal, systemd:UNIT, docker:CONTAINER or file:PATH.")
    logs_parser.add_argument("--json", action="store_true", help="List sources as JSON.")
    logs_parser.add_argument("-n", "--lines", type=int, default=200, help="Last N lines (default 200).")
    logs_parser.add_argument("--since", help="Only newer lines: 15m, 1h, 7d (not for files).")
    logs_parser.add_argument("--grep", help="Only lines containing this text (case-insensitive).")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Keep printing new lines.")
    logs_parser.set_defaults(func=_cmd_logs)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PermissionError as error:
        where = error.filename or "the meteorcli config directory"
        if Path(str(where)).name.startswith(".tmp"):
            where = Path(str(where)).parent  # an atomic write's temp file: name the directory

        print(f"error: permission denied: {where}", file=sys.stderr)
        if os.geteuid() != 0:
            print(
                f"{UNIT} keeps this device's identity in {cli_config.SYSTEM_CONFIG_DIR}. "
                f"Run the command with sudo, e.g. 'sudo {PROG} {args.command}'.",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
