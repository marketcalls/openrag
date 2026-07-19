"""embedding deployments

Revision ID: c4f6a8b0d2e3
Revises: b3e5f7a9c1d2
Create Date: 2026-07-20 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f6a8b0d2e3"
down_revision: str | Sequence[str] | None = "b3e5f7a9c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _install_lifecycle_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_embedding_deployment_lifecycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'building'
               OR NEW.total_versions <> 0
               OR NEW.completed_versions <> 0
               OR NEW.failed_versions <> 0
               OR NEW.scan_complete
               OR NEW.failure_code IS NOT NULL
               OR NEW.activated_by IS NOT NULL
               OR NEW.activated_at IS NOT NULL THEN
              RAISE EXCEPTION 'embedding deployment must begin in building state';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.profile_id <> OLD.profile_id
             OR NEW.generation_id <> OLD.generation_id
             OR NEW.requested_by <> OLD.requested_by
             OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'embedding deployment identity is immutable';
          END IF;
          IF NEW.total_versions < OLD.total_versions
             OR NEW.completed_versions < OLD.completed_versions
             OR NEW.failed_versions < OLD.failed_versions
             OR (OLD.scan_complete AND NOT NEW.scan_complete) THEN
            RAISE EXCEPTION 'embedding deployment progress cannot move backwards';
          END IF;
          IF OLD.scan_complete AND NEW.total_versions <> OLD.total_versions THEN
            RAISE EXCEPTION 'embedding deployment total is sealed after scan';
          END IF;
          IF OLD.status = 'failed' OR OLD.status = 'retired' THEN
            IF NEW IS DISTINCT FROM OLD THEN
              RAISE EXCEPTION 'terminal embedding deployment is immutable';
            END IF;
          ELSIF OLD.status = 'building'
                AND NEW.status NOT IN ('building','ready','failed') THEN
            RAISE EXCEPTION 'invalid embedding deployment transition';
          ELSIF OLD.status = 'ready'
                AND NEW.status NOT IN ('ready','active','failed') THEN
            RAISE EXCEPTION 'invalid embedding deployment transition';
          ELSIF OLD.status = 'active'
                AND NEW.status NOT IN ('active','retired') THEN
            RAISE EXCEPTION 'invalid embedding deployment transition';
          END IF;

          IF NEW.status IN ('ready','active')
             AND (NOT NEW.scan_complete
                  OR NEW.failed_versions <> 0
                  OR NEW.completed_versions <> NEW.total_versions) THEN
            RAISE EXCEPTION 'ready embedding deployment requires a complete clean scan';
          END IF;
          IF NEW.status = 'failed' AND NEW.failure_code IS NULL THEN
            RAISE EXCEPTION 'failed embedding deployment requires a failure code';
          END IF;
          IF NEW.status <> 'failed' AND NEW.failure_code IS NOT NULL THEN
            RAISE EXCEPTION 'only failed embedding deployments may have a failure code';
          END IF;
          IF NEW.status IN ('active','retired') THEN
            IF NEW.activated_by IS NULL OR NEW.activated_at IS NULL THEN
              RAISE EXCEPTION 'active embedding deployment requires activation authority';
            END IF;
          ELSIF NEW.activated_by IS NOT NULL OR NEW.activated_at IS NOT NULL THEN
            RAISE EXCEPTION 'inactive embedding deployment cannot have activation authority';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_embedding_deployment_lifecycle
        BEFORE INSERT OR UPDATE ON embedding_deployments
        FOR EACH ROW EXECUTE FUNCTION guard_embedding_deployment_lifecycle()
        """
    )


def upgrade() -> None:
    op.create_table(
        "embedding_deployments",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("activated_by", sa.Uuid(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("total_versions", sa.Integer(), nullable=False),
        sa.Column("completed_versions", sa.Integer(), nullable=False),
        sa.Column("failed_versions", sa.Integer(), nullable=False),
        sa.Column("scan_complete", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "total_versions >= 0 AND completed_versions >= 0 "
            "AND failed_versions >= 0 "
            "AND completed_versions + failed_versions <= total_versions",
            name="ck_embedding_deployments_counts",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR char_length(failure_code) BETWEEN 1 AND 100",
            name="ck_embedding_deployments_failure_code",
        ),
        sa.CheckConstraint(
            "status IN ('building','ready','active','failed','retired')",
            name="ck_embedding_deployments_status",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["embedding_profiles.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_id"),
    )
    op.create_index(
        op.f("ix_embedding_deployments_profile_id"),
        "embedding_deployments",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_embedding_deployments_status"),
        "embedding_deployments",
        ["status"],
        unique=False,
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_embedding_deployments_one_active "
        "ON embedding_deployments ((true)) WHERE status = 'active'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_embedding_deployments_one_pending "
        "ON embedding_deployments ((true)) "
        "WHERE status IN ('building','ready')"
    )
    _install_lifecycle_guard()


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE embedding_deployments IN ACCESS EXCLUSIVE MODE")
    )
    if connection.scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM embedding_deployments)")
    ):
        raise RuntimeError(
            "embedding deployment downgrade aborted: governed deployment exists"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_embedding_deployment_lifecycle "
        "ON embedding_deployments"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_embedding_deployment_lifecycle()")
    op.drop_index(
        "uq_embedding_deployments_one_pending",
        table_name="embedding_deployments",
    )
    op.drop_index(
        "uq_embedding_deployments_one_active",
        table_name="embedding_deployments",
    )
    op.drop_index(
        op.f("ix_embedding_deployments_status"),
        table_name="embedding_deployments",
    )
    op.drop_index(
        op.f("ix_embedding_deployments_profile_id"),
        table_name="embedding_deployments",
    )
    op.drop_table("embedding_deployments")
