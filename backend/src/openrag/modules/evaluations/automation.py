"""Pure scheduling contracts for bounded evaluation automation."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, select

from openrag.modules.evaluations.models import EvaluationPolicy

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def scheduled_trigger_key(due_at: datetime) -> str:
    normalized = _as_naive_utc(due_at)
    return f"scheduled:{normalized:%Y%m%dT%H%M%SZ}"


def config_trigger_key(fingerprint: str) -> str:
    if _DIGEST.fullmatch(fingerprint) is None:
        raise ValueError("evaluation configuration fingerprint is invalid")
    return f"config:{fingerprint}"


def workspace_model_fingerprint(model_id: UUID | None) -> str:
    return workspace_configuration_fingerprint(
        model_id,
        enrichment_enabled=False,
    )


def workspace_configuration_fingerprint(
    model_id: UUID | None,
    *,
    enrichment_enabled: bool,
) -> str:
    value = str(model_id) if model_id is not None else "none"
    enrichment = "enabled" if enrichment_enabled else "disabled"
    return hashlib.sha256(
        f"workspace-configuration:v2:{value}:{enrichment}".encode()
    ).hexdigest()


def next_scheduled_at(now: datetime, *, interval_hours: int) -> datetime:
    if not 1 <= interval_hours <= 720:
        raise ValueError("evaluation schedule interval is invalid")
    return _as_naive_utc(now) + timedelta(hours=interval_hours)


def build_due_policy_query(
    now: datetime,
    *,
    limit: int = 25,
) -> Select[tuple[EvaluationPolicy]]:
    if not 1 <= limit <= 100:
        raise ValueError("evaluation schedule claim limit is invalid")
    return (
        select(EvaluationPolicy)
        .where(
            EvaluationPolicy.enabled.is_(True),
            EvaluationPolicy.next_run_at <= _as_naive_utc(now),
        )
        .order_by(EvaluationPolicy.next_run_at, EvaluationPolicy.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
