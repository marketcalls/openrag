import logging

import pytest
import structlog
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

from openrag.core.logging import configure_logging, redact_sensitive


def test_redaction_processor() -> None:
    event = redact_sensitive(
        None,
        "",
        {"password": "hunter2", "api_key": "sk-123", "msg": "ok"},
    )
    assert event["password"] == "[REDACTED]"  # noqa: S105
    assert event["api_key"] == "[REDACTED]"
    assert event["msg"] == "ok"


def test_configure_logging_idempotent() -> None:
    configure_logging()
    configure_logging()
    assert structlog.is_configured()


def test_otel_log_export_is_structured_and_recursively_redacted() -> None:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    configure_logging(provider)

    structlog.get_logger("openrag.test").error(
        "provider_failed",
        api_key="sk-customer-secret",
        nested={"prompt": "confidential question", "code": "provider.timeout"},
    )
    provider.force_flush()

    records = exporter.get_finished_logs()
    assert len(records) == 1
    body = str(records[0].log_record.body)
    assert "provider_failed" in body
    assert "provider.timeout" in body
    assert "sk-customer-secret" not in body
    assert "confidential question" not in body
    assert body.count("[REDACTED]") >= 2
    provider.shutdown()


def test_nested_content_and_exception_secrets_never_reach_console_or_otlp(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets = [
        "Bearer customer-token",
        "invoice-confidential.pdf",
        "What is the private salary?",
        "retrieved payroll evidence",
        "remember my bank account",
        "provider exploded with api-key-123",
    ]
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    configure_logging(provider)

    try:
        raise RuntimeError(secrets[-1])
    except RuntimeError:
        logging.getLogger("openrag.security").error(
            {
                "event": "request_failed",
                "headers": {"authorization": secrets[0]},
                "document": {"filename": secrets[1]},
                "prompt": secrets[2],
                "nested": {
                    "retrieved_text": secrets[3],
                    "memory": [{"content": secrets[4]}],
                    "safe_code": "provider.timeout",
                },
            },
            exc_info=True,
        )
    provider.force_flush()

    console = capsys.readouterr().out
    exported = "\n".join(str(item.log_record.body) for item in exporter.get_finished_logs())
    combined = console + exported
    for secret in secrets:
        assert secret not in combined
    assert "provider.timeout" in combined
    assert "RuntimeError" in combined
    provider.shutdown()
