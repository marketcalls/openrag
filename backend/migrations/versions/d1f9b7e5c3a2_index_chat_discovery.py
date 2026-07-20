"""index chat discovery

Revision ID: d1f9b7e5c3a2
Revises: b7d5f3a1c9e8
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d1f9b7e5c3a2"
down_revision: str | Sequence[str] | None = "b7d5f3a1c9e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_chats_user_workspace_updated",
        "chats",
        ["user_id", "workspace_id", "updated_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chats_user_workspace_updated", table_name="chats")
