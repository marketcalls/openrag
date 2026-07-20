import logging
import sys
from collections.abc import Mapping, MutableMapping
from logging import INFO, LogRecord
from typing import Any

import structlog
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from structlog.typing import Processor

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


_MANAGED_HANDLER_IDS: set[int] = set()


class ContentSafeLogFilter(logging.Filter):
    """Ensure both console and OTLP handlers receive only bounded structured data."""

    def filter(self, record: LogRecord) -> bool:
        exception_type = None
        if record.exc_info is not None and record.exc_info[0] is not None:
            exception_type = record.exc_info[0].__name__
        if isinstance(record.msg, Mapping):
            safe = safe_log_fields(record.msg)
            record.msg = safe if isinstance(safe, dict) else {"event": "structured_log"}
        else:
            record.msg = {
                "event": "stdlib_log",
                "logger": record.name,
                "message_type": type(record.msg).__name__,
            }
        if exception_type is not None:
            record.msg["exception_type"] = exception_type
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def _openrag_handler(handler: logging.Handler) -> logging.Handler:
    handler.addFilter(ContentSafeLogFilter())
    _MANAGED_HANDLER_IDS.add(id(handler))
    return handler


def configure_logging(logger_provider: LoggerProvider | None = None) -> None:
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        redact_sensitive,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    root = logging.getLogger()
    for handler in list(root.handlers):
        if id(handler) in _MANAGED_HANDLER_IDS:
            root.removeHandler(handler)
    _MANAGED_HANDLER_IDS.clear()

    console = _openrag_handler(logging.StreamHandler(sys.stdout))
    console.setFormatter(formatter)
    root.addHandler(console)
    if logger_provider is not None:
        otel = _openrag_handler(LoggingHandler(level=INFO, logger_provider=logger_provider))
        otel.setFormatter(formatter)
        root.addHandler(otel)
    root.setLevel(INFO)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
