from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from openrag.modules.evaluations.automation import (
    build_due_policy_query,
    config_trigger_key,
    next_scheduled_at,
    scheduled_trigger_key,
)


def test_due_policy_claim_is_bounded_locked_and_ordered() -> None:
    statement = build_due_policy_query(datetime(2026, 7, 20, 0, 0), limit=25)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "evaluation_policies.enabled IS true" in sql
    assert "evaluation_policies.next_run_at <=" in sql
    assert "ORDER BY evaluation_policies.next_run_at, evaluation_policies.id" in sql
    assert "LIMIT 25" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_schedule_keys_are_deterministic_bounded_and_utc_normalized() -> None:
    aware = datetime(2026, 7, 20, 6, 30, tzinfo=UTC)

    assert scheduled_trigger_key(aware) == "scheduled:20260720T063000Z"
    assert next_scheduled_at(aware, interval_hours=24) == datetime(
        2026,
        7,
        21,
        6,
        30,
    )
    assert config_trigger_key("a" * 64) == f"config:{'a' * 64}"
    with pytest.raises(ValueError, match="fingerprint"):
        config_trigger_key("not-a-digest")
