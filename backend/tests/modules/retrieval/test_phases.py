import asyncio
import inspect
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from openrag.core.errors import UpstreamError
from openrag.modules.retrieval import service
from openrag.modules.retrieval.service import (
    RetrievalPlan,
    RetrievalResult,
    RetrievedChunk,
)
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext


class _Session:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def rollback(self) -> None:
        self.calls.append("rollback")


async def test_configured_generation_fallback_uses_postgres_revalidation(
    monkeypatch: Any,
) -> None:
    configured_generation = uuid4()
    active_generation = uuid4()
    scalar_values = iter((active_generation, uuid4()))

    class Session:
        async def scalar(self, statement: object) -> object:
            assert statement is not None
            return next(scalar_values)

    async def workspace(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(document_authority_enabled=False, min_score=0.35)

    configured_embedder = object()
    monkeypatch.setattr(service, "get_workspace_checked", workspace)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            authority_generation_id=configured_generation,
            embedding_model_id="openrag-hash-v1",
            embedding_dim=1024,
        ),
    )
    monkeypatch.setattr(
        service,
        "active_ingestion_profiles",
        lambda settings: SimpleNamespace(
            embedding_profile_version="embedding/v1/configured"
        ),
    )
    monkeypatch.setattr(service, "get_dense_embedder", lambda: configured_embedder)
    user_id = uuid4()
    org_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )

    plan = await service.prepare_configured_authority_fallback(
        Session(),  # type: ignore[arg-type]
        context,
        uuid4(),
        "list invoices",
    )

    assert plan is not None
    assert plan.authority_mode is True
    assert plan.dense_embedder is configured_embedder
    assert plan.current_approved is None
    assert configured_generation.hex in plan.collection


async def test_retrieve_releases_database_before_external_work(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []
    plan = RetrievalPlan(
        org_id=uuid4(),
        workspace_id=uuid4(),
        query="safe query",
        top_k=8,
        min_score=0.35,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=None,
        current_approved=True,
    )
    expected = RetrievalResult(chunks=[], no_answer=True)

    async def prepare(*args: object, **kwargs: object) -> object:
        calls.append("prepare")
        return plan

    async def execute(value: object) -> object:
        assert value is plan
        calls.append("external")
        return object()

    async def finalize(*args: object, **kwargs: object) -> RetrievalResult:
        calls.append("finalize")
        return expected

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "execute_retrieval", execute)
    monkeypatch.setattr(service, "finalize_retrieval", finalize)

    user_id = uuid4()
    org_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )
    result = await service.retrieve(
        _Session(calls),  # type: ignore[arg-type]
        context,
        uuid4(),
        "safe query",
    )

    assert result is expected
    assert calls == ["prepare", "rollback", "external", "finalize"]


async def test_migrated_workspace_prefers_active_authority_generation(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []
    legacy_plan = RetrievalPlan(
        org_id=uuid4(),
        workspace_id=uuid4(),
        query="what is codex?",
        top_k=8,
        min_score=0.35,
        authority_mode=False,
        embedding_model="legacy",
        collection="legacy",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=(),
        current_approved=None,
    )
    authority_plan = RetrievalPlan(
        org_id=legacy_plan.org_id,
        workspace_id=legacy_plan.workspace_id,
        query=legacy_plan.query,
        top_k=8,
        min_score=0.35,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=None,
        current_approved=True,
    )
    authority_external = object()
    legacy_external = object()
    authority_result = RetrievalResult(
        chunks=[RetrievedChunk(uuid4(), 1, 0, "current", 0.9)],
        no_answer=False,
    )
    legacy_result = RetrievalResult(
        chunks=[RetrievedChunk(uuid4(), 1, 0, "legacy", 0.8)],
        no_answer=False,
    )

    async def prepare(*args: object, **kwargs: object) -> RetrievalPlan:
        calls.append("prepare")
        return legacy_plan

    async def prepare_fallback(*args: object, **kwargs: object) -> RetrievalPlan:
        calls.append("fallback")
        return authority_plan

    async def prepare_configured_fallback(
        *args: object, **kwargs: object
    ) -> RetrievalPlan | None:
        return None

    async def execute(plan: RetrievalPlan) -> object:
        calls.append(f"external:{plan.collection}")
        return authority_external if plan.authority_mode else legacy_external

    async def finalize(
        session: object,
        context: object,
        plan: RetrievalPlan,
        external: object,
    ) -> RetrievalResult:
        calls.append(f"finalize:{plan.collection}")
        if plan.authority_mode:
            assert external is authority_external
            return authority_result
        assert external is legacy_external
        return legacy_result

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "prepare_authority_fallback", prepare_fallback)
    monkeypatch.setattr(
        service,
        "prepare_configured_authority_fallback",
        prepare_configured_fallback,
    )
    monkeypatch.setattr(service, "execute_retrieval", execute)
    monkeypatch.setattr(service, "finalize_retrieval", finalize)

    user_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=legacy_plan.org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=legacy_plan.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )
    result = await service.retrieve(
        _Session(calls),  # type: ignore[arg-type]
        context,
        legacy_plan.workspace_id,
        legacy_plan.query,
    )

    assert result is authority_result
    assert calls == [
        "prepare",
        "fallback",
        "rollback",
        "external:active",
        "external:legacy",
        "finalize:active",
        "rollback",
        "finalize:legacy",
    ]


async def test_broad_migrated_query_prefers_result_with_wider_document_coverage(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []
    workspace_id = uuid4()
    org_id = uuid4()
    legacy_plan = RetrievalPlan(
        org_id=org_id,
        workspace_id=workspace_id,
        query="list all invoices",
        top_k=8,
        min_score=0.35,
        authority_mode=False,
        embedding_model="legacy",
        collection="legacy",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=(),
        current_approved=None,
    )
    authority_plan = RetrievalPlan(
        org_id=org_id,
        workspace_id=workspace_id,
        query=legacy_plan.query,
        top_k=8,
        min_score=0.35,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=None,
        current_approved=True,
    )
    authority_result = RetrievalResult(
        chunks=[
            RetrievedChunk(
                uuid4(),
                1,
                0,
                "Algorithmic trading operational circular",
                0.9,
            )
        ],
        no_answer=False,
        best_dense_score=0.92,
    )
    legacy_result = RetrievalResult(
        chunks=[
            RetrievedChunk(uuid4(), 1, 0, "invoice one", 0.8),
            RetrievedChunk(uuid4(), 1, 0, "invoice two", 0.79),
            RetrievedChunk(uuid4(), 1, 0, "invoice three", 0.78),
        ],
        no_answer=False,
        best_dense_score=0.41,
    )

    async def prepare(*args: object, **kwargs: object) -> RetrievalPlan:
        return legacy_plan

    async def prepare_fallback(*args: object, **kwargs: object) -> RetrievalPlan:
        return authority_plan

    async def prepare_configured_fallback(
        *args: object, **kwargs: object
    ) -> RetrievalPlan | None:
        return None

    async def execute(plan: RetrievalPlan) -> object:
        calls.append(plan.collection)
        return object()

    async def finalize(
        session: object,
        context: object,
        plan: RetrievalPlan,
        external: object,
    ) -> RetrievalResult:
        return authority_result if plan.authority_mode else legacy_result

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "prepare_authority_fallback", prepare_fallback)
    monkeypatch.setattr(
        service,
        "prepare_configured_authority_fallback",
        prepare_configured_fallback,
    )
    monkeypatch.setattr(service, "execute_retrieval", execute)
    monkeypatch.setattr(service, "finalize_retrieval", finalize)
    user_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )

    result = await service.retrieve(
        _Session(calls),  # type: ignore[arg-type]
        context,
        workspace_id,
        legacy_plan.query,
    )

    assert result is legacy_result
    assert "active" in calls
    assert "legacy" in calls


async def test_entity_scoped_collection_query_prefers_stronger_generation_match(
    monkeypatch: Any,
) -> None:
    """A broad noun must not let unrelated high-coverage evidence win."""

    workspace_id = uuid4()
    org_id = uuid4()
    legacy_plan = RetrievalPlan(
        org_id=org_id,
        workspace_id=workspace_id,
        query="provide details about invoices related to IndiaCharts",
        top_k=8,
        min_score=0.35,
        authority_mode=False,
        embedding_model="legacy",
        collection="legacy",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=(),
        current_approved=None,
    )
    authority_plan = replace(
        legacy_plan,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        filtered_document_ids=None,
        current_approved=True,
    )
    unrelated_high_coverage = RetrievalResult(
        chunks=[
            RetrievedChunk(uuid4(), 1, 0, "BellTPO invoice", 0.4),
            RetrievedChunk(uuid4(), 1, 0, "Another invoice", 0.39),
            RetrievedChunk(uuid4(), 1, 0, "Third invoice", 0.38),
        ],
        no_answer=False,
        best_dense_score=0.91,
    )
    entity_match = RetrievalResult(
        chunks=[RetrievedChunk(uuid4(), 1, 0, "IndiaCharts invoice", 0.9)],
        no_answer=False,
        best_dense_score=0.41,
    )

    async def prepare(*args: object, **kwargs: object) -> RetrievalPlan:
        return legacy_plan

    async def prepare_fallback(*args: object, **kwargs: object) -> RetrievalPlan:
        return authority_plan

    async def no_configured_fallback(
        *args: object, **kwargs: object
    ) -> RetrievalPlan | None:
        return None

    async def execute(plan: RetrievalPlan) -> object:
        return object()

    async def finalize(
        session: object,
        context: object,
        plan: RetrievalPlan,
        external: object,
    ) -> RetrievalResult:
        return unrelated_high_coverage if plan.authority_mode else entity_match

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "prepare_authority_fallback", prepare_fallback)
    monkeypatch.setattr(
        service,
        "prepare_configured_authority_fallback",
        no_configured_fallback,
    )
    monkeypatch.setattr(service, "execute_retrieval", execute)
    monkeypatch.setattr(service, "finalize_retrieval", finalize)
    user_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )

    result = await service.retrieve(
        _Session([]),  # type: ignore[arg-type]
        context,
        workspace_id,
        legacy_plan.query,
    )

    assert result is entity_match


async def test_document_scope_is_applied_to_every_retrieval_generation(
    monkeypatch: Any,
) -> None:
    workspace_id = uuid4()
    org_id = uuid4()
    target_document_id = uuid4()
    legacy_plan = RetrievalPlan(
        org_id=org_id,
        workspace_id=workspace_id,
        query="invoice total",
        top_k=8,
        min_score=0.35,
        authority_mode=False,
        embedding_model="legacy",
        collection="legacy",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=(target_document_id, uuid4()),
        current_approved=None,
    )
    authority_plan = replace(
        legacy_plan,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        filtered_document_ids=None,
        current_approved=True,
    )
    searched: list[RetrievalPlan] = []

    async def prepare(*args: object, **kwargs: object) -> RetrievalPlan:
        return legacy_plan

    async def prepare_fallback(*args: object, **kwargs: object) -> RetrievalPlan:
        return authority_plan

    async def no_configured_fallback(
        *args: object, **kwargs: object
    ) -> RetrievalPlan | None:
        return None

    async def execute(plan: RetrievalPlan) -> object:
        searched.append(plan)
        return object()

    async def finalize(
        session: object,
        context: object,
        plan: RetrievalPlan,
        external: object,
    ) -> RetrievalResult:
        return RetrievalResult(chunks=[], no_answer=True)

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "prepare_authority_fallback", prepare_fallback)
    monkeypatch.setattr(
        service,
        "prepare_configured_authority_fallback",
        no_configured_fallback,
    )
    monkeypatch.setattr(service, "execute_retrieval", execute)
    monkeypatch.setattr(service, "finalize_retrieval", finalize)
    user_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )

    await service.retrieve(
        _Session([]),  # type: ignore[arg-type]
        context,
        workspace_id,
        legacy_plan.query,
        document_ids=(target_document_id,),
    )

    assert {plan.collection for plan in searched} == {"active", "legacy"}
    assert all(
        plan.filtered_document_ids == (target_document_id,) for plan in searched
    )


async def test_broad_query_searches_configured_and_active_authority_generations(
    monkeypatch: Any,
) -> None:
    workspace_id = uuid4()
    org_id = uuid4()
    legacy_plan = RetrievalPlan(
        org_id=org_id,
        workspace_id=workspace_id,
        query="list all invoices",
        top_k=8,
        min_score=0.35,
        authority_mode=False,
        embedding_model="legacy",
        collection="legacy",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=(),
        current_approved=None,
    )
    active_plan = replace(
        legacy_plan,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        filtered_document_ids=None,
        current_approved=True,
    )
    configured_plan = replace(
        active_plan,
        embedding_model="configured",
        collection="configured",
    )
    active_result = RetrievalResult(
        chunks=[RetrievedChunk(uuid4(), 1, 0, "new document", 0.9)],
        no_answer=False,
    )
    configured_result = RetrievalResult(
        chunks=[
            RetrievedChunk(uuid4(), 1, 0, "invoice one", 0.8),
            RetrievedChunk(uuid4(), 1, 0, "invoice two", 0.79),
            RetrievedChunk(uuid4(), 1, 0, "invoice three", 0.78),
        ],
        no_answer=False,
    )
    legacy_result = RetrievalResult(chunks=[], no_answer=True)
    searched: list[str] = []

    async def prepare(*args: object, **kwargs: object) -> RetrievalPlan:
        return legacy_plan

    async def prepare_active(*args: object, **kwargs: object) -> RetrievalPlan:
        return active_plan

    async def prepare_configured(
        *args: object, **kwargs: object
    ) -> RetrievalPlan:
        return configured_plan

    async def execute(plan: RetrievalPlan) -> object:
        searched.append(plan.collection)
        return object()

    async def finalize(
        session: object,
        context: object,
        plan: RetrievalPlan,
        external: object,
    ) -> RetrievalResult:
        return {
            "active": active_result,
            "configured": configured_result,
            "legacy": legacy_result,
        }[plan.collection]

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "prepare_authority_fallback", prepare_active)
    monkeypatch.setattr(
        service,
        "prepare_configured_authority_fallback",
        prepare_configured,
    )
    monkeypatch.setattr(service, "execute_retrieval", execute)
    monkeypatch.setattr(service, "finalize_retrieval", finalize)
    user_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )

    result = await service.retrieve(
        _Session([]),  # type: ignore[arg-type]
        context,
        workspace_id,
        legacy_plan.query,
    )

    assert result is configured_result
    assert set(searched) == {"active", "configured", "legacy"}


async def test_external_vector_failure_is_sanitized(monkeypatch: Any) -> None:
    class Embedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2]]

    class Qdrant:
        async def query_points(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("private qdrant response details")

    plan = RetrievalPlan(
        org_id=uuid4(),
        workspace_id=uuid4(),
        query="list invoices",
        top_k=8,
        min_score=0.35,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        dense_embedder=Embedder(),  # type: ignore[arg-type]
        filtered_document_ids=None,
        current_approved=True,
    )
    monkeypatch.setattr(service, "get_qdrant", lambda: Qdrant())

    with pytest.raises(UpstreamError, match="retrieval service unavailable") as raised:
        await service.execute_retrieval(plan)

    assert "private qdrant" not in str(raised.value)


async def test_migrated_retrieval_never_swallows_cancellation(monkeypatch: Any) -> None:
    workspace_id = uuid4()
    org_id = uuid4()
    legacy_plan = RetrievalPlan(
        org_id=org_id,
        workspace_id=workspace_id,
        query="list invoices",
        top_k=8,
        min_score=0.35,
        authority_mode=False,
        embedding_model="legacy",
        collection="legacy",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=(),
        current_approved=None,
    )
    authority_plan = RetrievalPlan(
        org_id=org_id,
        workspace_id=workspace_id,
        query=legacy_plan.query,
        top_k=8,
        min_score=0.35,
        authority_mode=True,
        embedding_model="active",
        collection="active",
        dense_embedder=object(),  # type: ignore[arg-type]
        filtered_document_ids=None,
        current_approved=True,
    )

    async def prepare(*args: object, **kwargs: object) -> RetrievalPlan:
        return legacy_plan

    async def prepare_fallback(*args: object, **kwargs: object) -> RetrievalPlan:
        return authority_plan

    async def prepare_configured_fallback(
        *args: object, **kwargs: object
    ) -> RetrievalPlan | None:
        return None

    async def execute(plan: RetrievalPlan) -> object:
        if plan.authority_mode:
            raise asyncio.CancelledError
        return object()

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "prepare_authority_fallback", prepare_fallback)
    monkeypatch.setattr(
        service,
        "prepare_configured_authority_fallback",
        prepare_configured_fallback,
    )
    monkeypatch.setattr(service, "execute_retrieval", execute)
    user_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await service.retrieve(
            _Session([]),  # type: ignore[arg-type]
            context,
            workspace_id,
            legacy_plan.query,
        )


def test_external_retrieval_phase_cannot_receive_a_sql_session() -> None:
    parameters = inspect.signature(service.execute_retrieval).parameters

    assert tuple(parameters) == ("plan",)
    assert parameters["plan"].annotation in {RetrievalPlan, "RetrievalPlan"}
