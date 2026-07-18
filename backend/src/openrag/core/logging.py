import re
from collections.abc import MutableMapping
from logging import INFO
from typing import Any

import structlog

_SENSITIVE = re.compile(r"password|secret|token|api_key|key$", re.IGNORECASE)


def redact_sensitive(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if _SENSITIVE.search(key):
            event_dict[key] = "[REDACTED]"
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
