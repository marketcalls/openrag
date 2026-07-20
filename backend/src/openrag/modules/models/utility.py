from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.models.models import Model


async def resolve_utility_model(session: AsyncSession) -> Model | None:
    """Return the one measured model designated for bounded background AI work."""

    return (
        await session.execute(
            select(Model).where(
                Model.is_utility.is_(True),
                Model.enabled.is_(True),
                Model.probe_status == "passed",
                Model.supports_chat_completion.is_(True),
                Model.supports_streaming.is_(True),
            )
        )
    ).scalar_one_or_none()
