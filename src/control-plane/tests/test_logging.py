"""Structured logging configuration tests."""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest
import structlog

from app.core.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot and restore root logger state so tests don't leak into each other."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.handlers.clear()
    for handler in original_handlers:
        root.addHandler(handler)
    root.setLevel(original_level)


def test_configure_logging_json_emits_parseable_json(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    configure_logging(level="INFO", log_format="json")

    logger = structlog.get_logger("test.json.logger")
    logger.info("hello", foo="bar")

    lines = [line for line in buffer.getvalue().strip().splitlines() if line]
    assert lines, "expected at least one log line"
    payload = json.loads(lines[-1])

    assert payload["event"] == "hello"
    assert payload["foo"] == "bar"
    assert payload["level"] == "info"


def test_configure_logging_console_is_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    configure_logging(level="INFO", log_format="console")

    logger = structlog.get_logger("test.console.logger")
    logger.info("hello console")

    output = buffer.getvalue().strip()
    assert "hello console" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.splitlines()[-1])


def test_configure_logging_is_case_insensitive_for_format(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    configure_logging(level="INFO", log_format="JSON")

    logger = structlog.get_logger("test.upper.logger")
    logger.info("upper case format")

    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert payload["event"] == "upper case format"


def test_configure_logging_sets_root_level() -> None:
    configure_logging(level="DEBUG", log_format="console")
    assert logging.getLogger().level == logging.DEBUG

    configure_logging(level="warning", log_format="console")
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_replaces_handlers_instead_of_accumulating() -> None:
    configure_logging(level="INFO", log_format="console")
    configure_logging(level="INFO", log_format="console")
    configure_logging(level="INFO", log_format="console")

    assert len(logging.getLogger().handlers) == 1


def test_configure_logging_quiets_noisy_loggers() -> None:
    configure_logging(level="INFO", log_format="console")

    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
