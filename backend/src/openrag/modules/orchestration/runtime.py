"""Application-facing orchestration factories with provider adapters hidden."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.modules.chat.llm import LLMStreamer
from openrag.modules.models.models import Model
from openrag.modules.models.reasoning import ReasoningEffort
from openrag.modules.orchestration.agent_gather import (
    AgentGatherer,
    AgentGathererFactory,
)
from openrag.modules.orchestration.agno_litellm import AgnoLiteLLMStreamer
from openrag.modules.orchestration.agno_planner import AgnoPlanner
from openrag.modules.orchestration.model_gateway import (
    ModelRuntime,
    resolve_model_runtime,
)
from openrag.modules.orchestration.retrieval_tools import (
    RetrievalToolExecutor,
    TenantRetrievalBackend,
)
from openrag.modules.tenancy.context import TenantContext


@dataclass(frozen=True, slots=True)
class ModelExecution:
    streamer: LLMStreamer
    agent_gatherer_factory: AgentGathererFactory | None


def build_agent_gatherer_factory(
    runtime: ModelRuntime,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    context: TenantContext,
    workspace_id: UUID,
    structured_output_measured: bool,
) -> AgentGathererFactory | None:
    """Compose tenant-pinned tools only for a measured planner model."""

    if not structured_output_measured:
        return None

    def build(query: str) -> AgentGatherer:
        executor = RetrievalToolExecutor(
            TenantRetrievalBackend(session_factory, context, workspace_id),
            org_id=context.org_id,
            workspace_id=workspace_id,
        )
        return AgentGatherer(
            AgnoPlanner(
                runtime,
                query=query,
                enabled_tools=("search", "search_by_metadata", "get_document"),
            ),
            executor,
        )

    return build


async def create_model_execution(
    session: AsyncSession,
    model: Model,
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    context: TenantContext,
    workspace_id: UUID,
    reasoning_effort: ReasoningEffort = "off",
) -> ModelExecution:
    runtime = await resolve_model_runtime(
        session,
        model,
        settings,
        reasoning_effort=reasoning_effort,
    )
    return ModelExecution(
        streamer=AgnoLiteLLMStreamer(runtime),
        agent_gatherer_factory=build_agent_gatherer_factory(
            runtime,
            session_factory=session_factory,
            context=context,
            workspace_id=workspace_id,
            structured_output_measured=(
                model.probe_status == "passed" and model.supports_structured_json
            ),
        ),
    )


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
