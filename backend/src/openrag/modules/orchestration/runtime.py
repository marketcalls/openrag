"""Application-facing orchestration factories with provider adapters hidden."""

from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.modules.chat.llm import LLMStreamer
from openrag.modules.models.models import Model
from openrag.modules.models.reasoning import ReasoningEffort
from openrag.modules.orchestration.agno_litellm import AgnoLiteLLMStreamer
from openrag.modules.orchestration.model_gateway import resolve_model_runtime


async def create_model_streamer(
    session: AsyncSession,
    model: Model,
    settings: Settings,
    *,
    reasoning_effort: ReasoningEffort = "off",
) -> LLMStreamer:
    return AgnoLiteLLMStreamer(
        await resolve_model_runtime(
            session,
            model,
            settings,
            reasoning_effort=reasoning_effort,
        )
    )
