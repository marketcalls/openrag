"""Platform-superadmin workflows for governed RAG evaluation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.modules.evaluations import service
from openrag.modules.evaluations.schemas import (
    EvaluationDatasetCreate,
    EvaluationDatasetOut,
    EvaluationDatasetVersionCreate,
    EvaluationDatasetVersionDetail,
    EvaluationDatasetVersionOut,
    EvaluationPolicyOut,
    EvaluationPolicyUpsert,
    EvaluationRunCreate,
    EvaluationRunDetail,
    EvaluationRunOut,
)
from openrag.modules.tenancy.context import TenantContext, require_platform_superadmin

router = APIRouter(prefix="/admin/evaluations", tags=["evaluations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SuperadminDep = Annotated[TenantContext, Depends(require_platform_superadmin())]


@router.get("/policies", response_model=list[EvaluationPolicyOut])
async def list_policies(
    session: SessionDep,
    context: SuperadminDep,
    workspace_id: UUID | None = None,
) -> list[EvaluationPolicyOut]:
    return [
        EvaluationPolicyOut.model_validate(policy)
        for policy in await service.list_policies(session, context, workspace_id)
    ]


@router.put("/policies", response_model=EvaluationPolicyOut)
async def upsert_policy(
    body: EvaluationPolicyUpsert,
    session: SessionDep,
    context: SuperadminDep,
) -> EvaluationPolicyOut:
    return EvaluationPolicyOut.model_validate(
        await service.upsert_policy(session, context, body)
    )


@router.get("/datasets", response_model=list[EvaluationDatasetOut])
async def list_datasets(
    session: SessionDep,
    context: SuperadminDep,
    workspace_id: UUID | None = None,
) -> list[EvaluationDatasetOut]:
    return [
        EvaluationDatasetOut.model_validate(dataset)
        for dataset in await service.list_datasets(session, context, workspace_id)
    ]


@router.post("/datasets", status_code=201, response_model=EvaluationDatasetOut)
async def create_dataset(
    body: EvaluationDatasetCreate,
    session: SessionDep,
    context: SuperadminDep,
) -> EvaluationDatasetOut:
    return EvaluationDatasetOut.model_validate(
        await service.create_dataset(session, context, body)
    )


@router.get(
    "/datasets/{dataset_id}/versions",
    response_model=list[EvaluationDatasetVersionOut],
)
async def list_dataset_versions(
    dataset_id: UUID,
    session: SessionDep,
    context: SuperadminDep,
) -> list[EvaluationDatasetVersionOut]:
    return [
        EvaluationDatasetVersionOut.model_validate(version)
        for version in await service.list_dataset_versions(
            session,
            context,
            dataset_id,
        )
    ]


@router.post(
    "/datasets/{dataset_id}/versions",
    status_code=201,
    response_model=EvaluationDatasetVersionOut,
)
async def create_dataset_version(
    dataset_id: UUID,
    body: EvaluationDatasetVersionCreate,
    session: SessionDep,
    context: SuperadminDep,
) -> EvaluationDatasetVersionOut:
    return EvaluationDatasetVersionOut.model_validate(
        await service.create_dataset_version(session, context, dataset_id, body)
    )


@router.get(
    "/versions/{version_id}",
    response_model=EvaluationDatasetVersionDetail,
)
async def get_dataset_version(
    version_id: UUID,
    session: SessionDep,
    context: SuperadminDep,
) -> EvaluationDatasetVersionDetail:
    return await service.get_dataset_version_detail(session, context, version_id)


@router.get("/runs", response_model=list[EvaluationRunOut])
async def list_runs(
    session: SessionDep,
    context: SuperadminDep,
    dataset_version_id: UUID | None = None,
) -> list[EvaluationRunOut]:
    return [
        EvaluationRunOut.model_validate(run)
        for run in await service.list_runs(session, context, dataset_version_id)
    ]


@router.post("/runs", status_code=202, response_model=EvaluationRunOut)
async def create_run(
    body: EvaluationRunCreate,
    session: SessionDep,
    context: SuperadminDep,
) -> EvaluationRunOut:
    return EvaluationRunOut.model_validate(
        await service.create_run(session, context, body)
    )


@router.get("/runs/{run_id}", response_model=EvaluationRunDetail)
async def get_run(
    run_id: UUID,
    session: SessionDep,
    context: SuperadminDep,
) -> EvaluationRunDetail:
    return await service.get_run_detail(session, context, run_id)
