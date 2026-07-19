"""harden transactional outbox

Revision ID: a4f87e62b913
Revises: 4b8e0f7a3c21
Create Date: 2026-07-19 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4f87e62b913"
down_revision: str | Sequence[str] | None = "4b8e0f7a3c21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SAFE_ERROR_CODES = (
    "legacy_dispatch_failure",
    "contract_invalid",
    "schema_not_registered",
    "event_not_authoritative",
    "event_transport_unavailable",
    "event_durability_unconfirmed",
)


def _preflight() -> None:
    connection = op.get_bind()
    invalid_query = sa.text(
        "SELECT EXISTS ("
        "SELECT 1 FROM outbox_events WHERE "
        "attempts < 0 "
        "OR char_length(event_type) NOT BETWEEN 1 AND 200 "
        "OR char_length(aggregate_type) NOT BETWEEN 1 AND 120 "
        "OR char_length(dedupe_key) NOT BETWEEN 1 AND 255 "
        "OR (lease_owner IS NOT NULL AND char_length(lease_owner) NOT BETWEEN 1 AND 128) "
        "OR ((lease_owner IS NULL) <> (lease_expires_at IS NULL)) "
        "OR json_typeof(payload) <> 'object' "
        "OR pg_column_size(payload) > 16384"
        ")"
    )
    invalid = connection.scalar(invalid_query)
    if invalid:
        connection.execute(
            sa.text("DO $$ BEGIN RAISE EXCEPTION 'OPENRAG_OUTBOX_PREFLIGHT_FAILED'; END $$")
        )


def upgrade() -> None:
    _preflight()

    op.alter_column(
        "outbox_events",
        "event_type",
        existing_type=sa.String(),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    op.alter_column(
        "outbox_events",
        "aggregate_type",
        existing_type=sa.String(),
        type_=sa.String(length=120),
        existing_nullable=False,
    )
    op.alter_column(
        "outbox_events",
        "dedupe_key",
        existing_type=sa.String(),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "outbox_events",
        "lease_owner",
        existing_type=sa.String(),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.add_column(
        "outbox_events",
        sa.Column("dispatch_after", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("lease_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("dead_lettered_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("envelope_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("published_stream", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("published_message_id", sa.String(length=128), nullable=True),
    )
    op.execute(
        "UPDATE outbox_events "
        "SET dispatch_after=created_at, "
        "last_error_code=CASE WHEN last_error IS NULL THEN NULL "
        "ELSE 'legacy_dispatch_failure' END, "
        "lease_owner=NULL, lease_expires_at=NULL, lease_token=NULL"
    )
    op.alter_column(
        "outbox_events",
        "dispatch_after",
        existing_type=sa.DateTime(),
        nullable=False,
    )
    op.drop_column("outbox_events", "last_error")

    op.create_check_constraint(
        "ck_outbox_events_attempts_nonnegative",
        "outbox_events",
        "attempts >= 0",
    )
    op.create_check_constraint(
        "ck_outbox_events_terminal_exclusive",
        "outbox_events",
        "NOT (published_at IS NOT NULL AND dead_lettered_at IS NOT NULL)",
    )
    quoted_codes = ",".join(f"'{code}'" for code in _SAFE_ERROR_CODES)
    op.create_check_constraint(
        "ck_outbox_events_safe_error_code",
        "outbox_events",
        f"last_error_code IS NULL OR last_error_code IN ({quoted_codes})",
    )
    op.create_check_constraint(
        "ck_outbox_events_envelope_digest",
        "outbox_events",
        "envelope_digest IS NULL OR envelope_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_outbox_events_payload_bounded",
        "outbox_events",
        "json_typeof(payload) = 'object' AND pg_column_size(payload) <= 16384",
    )
    op.create_check_constraint(
        "ck_outbox_events_lease_complete",
        "outbox_events",
        "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
        "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
        "AND lease_expires_at IS NOT NULL)",
    )
    op.create_index(
        "ix_outbox_events_claimable",
        "outbox_events",
        ["dispatch_after", "created_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL AND dead_lettered_at IS NULL"),
    )
    op.create_index(
        "ix_outbox_events_lease_token",
        "outbox_events",
        ["lease_token"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_dead_lettered_at",
        "outbox_events",
        ["dead_lettered_at"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_dispatch_after",
        "outbox_events",
        ["dispatch_after"],
        unique=False,
    )


def downgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("last_error", sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE outbox_events SET last_error=last_error_code WHERE last_error_code IS NOT NULL"
    )

    op.drop_index("ix_outbox_events_dispatch_after", table_name="outbox_events")
    op.drop_index("ix_outbox_events_dead_lettered_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_lease_token", table_name="outbox_events")
    op.drop_index("ix_outbox_events_claimable", table_name="outbox_events")
    op.drop_constraint("ck_outbox_events_lease_complete", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_events_payload_bounded", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_events_envelope_digest", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_events_safe_error_code", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_events_terminal_exclusive", "outbox_events", type_="check")
    op.drop_constraint("ck_outbox_events_attempts_nonnegative", "outbox_events", type_="check")

    for column in (
        "published_message_id",
        "published_stream",
        "envelope_digest",
        "last_error_code",
        "dead_lettered_at",
        "lease_token",
        "dispatch_after",
    ):
        op.drop_column("outbox_events", column)

    op.alter_column(
        "outbox_events",
        "lease_owner",
        existing_type=sa.String(length=128),
        type_=sa.String(),
        existing_nullable=True,
    )
    op.alter_column(
        "outbox_events",
        "dedupe_key",
        existing_type=sa.String(length=255),
        type_=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "outbox_events",
        "aggregate_type",
        existing_type=sa.String(length=120),
        type_=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "outbox_events",
        "event_type",
        existing_type=sa.String(length=200),
        type_=sa.String(),
        existing_nullable=False,
    )
