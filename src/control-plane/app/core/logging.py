"""Structured logging configuration (JSON in deploy, console locally)."""

from __future__ import annotations

import logging
import re
import sys

import structlog
from structlog.stdlib import ProcessorFormatter

_TICKET_QUERY = re.compile(r"(\bticket=)[^&\s\"]+")


class RedactTicketFilter(logging.Filter):
    """uvicorn logs WebSocket paths with their query; hide ``?ticket=`` from older shell clients."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TICKET_QUERY.sub(r"\1[redacted]", arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def configure_logging(level: str = "INFO", log_format: str = "console") -> None:
    """Configure stdlib logging and structlog to share one renderer."""
    shared: list[object] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    use_json = log_format.lower() == "json"
    renderer: object = structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer()

    formatter = ProcessorFormatter(
        processors=[
            ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(handler)

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name in ("uvicorn.error", "uvicorn.access"):
        named = logging.getLogger(name)
        if not any(isinstance(item, RedactTicketFilter) for item in named.filters):
            named.addFilter(RedactTicketFilter())
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
