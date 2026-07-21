"""Application-facing orchestration factories with provider adapters hidden."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.modules.chat.llm import LLMStreamer
from openrag.modules.grounding.models import GroundingPolicy
from openrag.modules.grounding.service import provision_default_grounding_policy
from openrag.modules.models.models import Model
from openrag.modules.models.reasoning import ReasoningEffort
from openrag.modules.orchestration.agent_gather import (
    AgentGatherer,
    AgentGathererFactory,
)
from openrag.modules.orchestration.agno_analytics import (
    AgnoAnalyticsComposer,
    AnalyticsComposer,
)
from openrag.modules.orchestration.agno_litellm import AgnoLiteLLMStreamer
from openrag.modules.orchestration.agno_planner import AgnoPlanner
from openrag.modules.orchestration.agno_validator import (
    AgnoStructuredVerifierStreamer,
)
from openrag.modules.orchestration.answer_validation import (
    BoundAnswerValidator,
    StrictAnswerValidator,
)
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
    answer_validator: BoundAnswerValidator | None
    analytics_composer: AnalyticsComposer | None


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
    document_authority_enabled: bool,
    reasoning_effort: ReasoningEffort = "off",
) -> ModelExecution:
    runtime = await resolve_model_runtime(
        session,
        model,
        settings,
        reasoning_effort=reasoning_effort,
    )
    answer_validator: BoundAnswerValidator | None = None
    if document_authority_enabled:
        now = naive_utc()
        policy = await session.scalar(
            select(GroundingPolicy).where(
                GroundingPolicy.org_id == context.org_id,
                GroundingPolicy.workspace_id == workspace_id,
                GroundingPolicy.status == "active",
                or_(
                    GroundingPolicy.effective_at.is_(None),
                    GroundingPolicy.effective_at <= now,
                ),
                or_(
                    GroundingPolicy.expires_at.is_(None),
                    GroundingPolicy.expires_at > now,
                ),
            )
        )
        if policy is None:
            policy = await provision_default_grounding_policy(
                session,
                org_id=context.org_id,
                workspace_id=workspace_id,
                created_by=context.user_id,
            )
        if policy is not None:
            verifier_model = await session.get(Model, policy.verifier_model_id)
            validator: StrictAnswerValidator | None = None
            if (
                verifier_model is not None
                and verifier_model.enabled
                and verifier_model.probe_status == "passed"
                and verifier_model.supports_structured_json
                and verifier_model.supports_verifier
            ):
                verifier_runtime = await resolve_model_runtime(
                    session,
                    verifier_model,
                    settings,
                )
                validator = StrictAnswerValidator(
                    AgnoStructuredVerifierStreamer(verifier_runtime),
                    model_name=verifier_model.litellm_model_name,
                    entailment_threshold=policy.entailment_threshold,
                )
            answer_validator = BoundAnswerValidator(
                policy_id=policy.id,
                policy_version=policy.policy_version,
                verifier_model_id=policy.verifier_model_id,
                validate_call=validator.validate if validator is not None else None,
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
        answer_validator=answer_validator,
        analytics_composer=(
            AgnoAnalyticsComposer(runtime)
            if model.probe_status == "passed" and model.supports_structured_json
            else None
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
