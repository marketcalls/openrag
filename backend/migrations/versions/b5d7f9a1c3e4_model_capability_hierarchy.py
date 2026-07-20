"""Make chat capability explicit and enforce evaluator capability hierarchy.

Revision ID: b5d7f9a1c3e4
Revises: a4c6e8f0b2d3
Create Date: 2026-07-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5d7f9a1c3e4"
down_revision: str | None = "a4c6e8f0b2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE models SET supports_chat_completion = true")
    op.create_check_constraint(
        "ck_models_capability_hierarchy",
        "models",
        "(NOT supports_structured_json OR supports_chat_completion) "
        "AND (NOT supports_verifier OR supports_structured_json)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_models_capability_hierarchy",
        "models",
        type_="check",
    )
