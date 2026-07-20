from collections.abc import MutableMapping
from logging import INFO
from typing import Any

import structlog

from openrag.core.telemetry import safe_log_fields


def redact_sensitive(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    safe = safe_log_fields(event_dict)
    event_dict.clear()
    if isinstance(safe, dict):
        event_dict.update(safe)
    return event_dict


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(INFO),
        cache_logger_on_first_use=True,
    )
