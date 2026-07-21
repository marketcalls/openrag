from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base


class OrgQuota(Base):
    __tablename__ = "org_quotas"
    __table_args__ = (
        CheckConstraint("monthly_tokens >= 0", name="ck_org_quotas_monthly_tokens"),
        CheckConstraint(
            "default_user_monthly_tokens IS NULL OR default_user_monthly_tokens >= 0",
            name="ck_org_quotas_default_user_tokens",
        ),
        CheckConstraint("reset_day BETWEEN 1 AND 31", name="ck_org_quotas_reset_day"),
    )

    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    monthly_tokens: Mapped[int]
    default_user_monthly_tokens: Mapped[int | None] = mapped_column(default=None)
    reset_day: Mapped[int] = mapped_column(default=1)


class UserQuota(Base):
    __tablename__ = "user_quotas"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_user_quotas_org_user"),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_user_quotas_org_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("monthly_tokens >= 0", name="ck_user_quotas_monthly_tokens"),
    )

    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    monthly_tokens: Mapped[int]
