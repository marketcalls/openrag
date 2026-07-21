import inspect
from typing import Any
from uuid import uuid4

from openrag.modules.retrieval import service
from openrag.modules.retrieval.service import RetrievalPlan, RetrievalResult
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext


class _Session:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def rollback(self) -> None:
        self.calls.append("rollback")


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
    authority_result = RetrievalResult(chunks=[], no_answer=False)

    async def prepare(*args: object, **kwargs: object) -> RetrievalPlan:
        calls.append("prepare")
        return legacy_plan

    async def prepare_fallback(*args: object, **kwargs: object) -> RetrievalPlan:
        calls.append("fallback")
        return authority_plan

    async def execute(plan: RetrievalPlan) -> object:
        calls.append(f"external:{plan.collection}")
        assert plan.authority_mode
        return authority_external

    async def finalize(
        session: object,
        context: object,
        plan: RetrievalPlan,
        external: object,
    ) -> RetrievalResult:
        calls.append(f"finalize:{plan.collection}")
        assert plan.authority_mode
        assert external is authority_external
        return authority_result

    monkeypatch.setattr(service, "prepare_retrieval", prepare)
    monkeypatch.setattr(service, "prepare_authority_fallback", prepare_fallback)
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
        "finalize:active",
    ]


def test_external_retrieval_phase_cannot_receive_a_sql_session() -> None:
    parameters = inspect.signature(service.execute_retrieval).parameters

    assert tuple(parameters) == ("plan",)
    assert parameters["plan"].annotation in {RetrievalPlan, "RetrievalPlan"}
