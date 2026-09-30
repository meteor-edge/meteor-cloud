"""Command-line entry point for the Edge Platform reference agent.

Commands:
  register  Register this device using a registration token.
  run       Send periodic heartbeats using the stored credential.
  info      Show the persisted (non-secret) configuration.

Credentials are never printed by any command.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from edge_agent.client import AgentApiError, EdgeClient
from edge_agent.config import AgentConfig, AgentPaths, load_config
from edge_agent.credentials import read_device_token, read_registration_token_file
from edge_agent.heartbeat import run_loop, send_heartbeat
from edge_agent.registration import register

logger = logging.getLogger("edge_agent")


def _paths_from_args(args: argparse.Namespace) -> AgentPaths:
    if args.config_dir:
        base = Path(args.config_dir)
        return AgentPaths(config_path=base / "config.json", token_path=base / "device-token")
    return AgentPaths.default()


def _cmd_register(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    token_file: Path | None = None
    if args.token:
        token = args.token
    else:
        token_file = Path(args.token_file)
        token = read_registration_token_file(token_file)

    client = EdgeClient(args.server)
    try:
        result = register(
            client=client,
            paths=paths,
            token=token,
            name=args.name,
            token_file=token_file,
        )
    except AgentApiError as error:
        logger.error("Registration failed: %s", error.code)
        print(f"Registration failed: {error.message}", file=sys.stderr)
        return 1

    print(f"Registered device {result.device_id}")
    print(f"Organization: {result.organization_id}")
    print(f"Name: {result.name}")
    print(f"Heartbeat interval: {result.heartbeat_interval_seconds}s")
    print(f"Credential stored at: {paths.token_path}")
    return 0


def _load_or_fail(paths: AgentPaths) -> tuple[AgentConfig, str] | None:
    config = load_config(paths)
    if config is None:
        print("Agent is not registered. Run 'edge-agent register' first.", file=sys.stderr)
        return None
    device_token = read_device_token(paths.token_path)
    if device_token is None:
        print("Device credential is missing. Re-register the agent.", file=sys.stderr)
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
            print(f"Heartbeat failed: {error.message}", file=sys.stderr)
            return 1
        print(f"Heartbeat ok; status: {response.get('status', 'unknown')}")
        return 0

    logger.info("Starting heartbeat loop every %ss", config.heartbeat_interval_seconds)
    mqtt_session = None
    from edge_agent.mqtt import DeviceMqttSession
    from edge_agent.mqtt_config import read_mqtt_config

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
            interval_seconds=config.heartbeat_interval_seconds,
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


def _cmd_info(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    config = load_config(paths)
    if config is None:
        print("Agent is not registered.")
        return 1
    has_credential = read_device_token(paths.token_path) is not None
    print(f"Server: {config.server_url}")
    print(f"Device ID: {config.device_id}")
    print(f"Organization: {config.organization_id}")
    print(f"Name: {config.name}")
    print(f"Heartbeat interval: {config.heartbeat_interval_seconds}s")
    print(f"Credential present: {'yes' if has_credential else 'no'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edge-agent", description=__doc__)
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Directory for agent config and credential (default: /etc/edge-agent).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register", help="Register this device.")
    register_parser.add_argument("--server", required=True, help="Control-plane base URL.")
    token_group = register_parser.add_mutually_exclusive_group(required=True)
    token_group.add_argument("--token", help="Registration token value.")
    token_group.add_argument(
        "--token-file",
        help="Path to a file containing the registration token (recommended).",
    )
    register_parser.add_argument("--name", default=None, help="Optional device name.")
    register_parser.set_defaults(func=_cmd_register)

    run_parser = subparsers.add_parser("run", help="Send heartbeats.")
    run_parser.add_argument("--once", action="store_true", help="Send a single heartbeat and exit.")
    run_parser.set_defaults(func=_cmd_run)

    info_parser = subparsers.add_parser("info", help="Show persisted configuration.")
    info_parser.set_defaults(func=_cmd_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
