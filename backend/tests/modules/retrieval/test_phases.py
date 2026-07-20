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
    plan = object()
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


def test_external_retrieval_phase_cannot_receive_a_sql_session() -> None:
    parameters = inspect.signature(service.execute_retrieval).parameters

    assert tuple(parameters) == ("plan",)
    assert parameters["plan"].annotation in {RetrievalPlan, "RetrievalPlan"}
