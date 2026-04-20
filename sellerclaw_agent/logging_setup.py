from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

_configured = False


def configure_agent_logging() -> None:
    """Configure structlog once (idempotent). Safe for tests and local dev."""
    global _configured  # noqa: PLW0603
    if _configured:
        return
    _configured = True

    raw = (os.environ.get("SELLERCLAW_AGENT_LOG_FORMAT") or "human").strip().lower()
    use_json = raw in {"json", "1", "true", "yes", "on"}

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if use_json:
        processors: list[Any] = [
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    level_name = (os.environ.get("SELLERCLAW_AGENT_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )
