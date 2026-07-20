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
