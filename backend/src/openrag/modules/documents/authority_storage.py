"""Strict, inert provisioning for version-scoped authority vector storage."""

from dataclasses import dataclass
from uuid import UUID

from qdrant_client import AsyncQdrantClient, models

from openrag.modules.retrieval.client import get_qdrant

_ACTIVE_ALIAS = "openrag_authority_active_v1"
_PAYLOAD_INDEXES = {
    "tenant_id": models.PayloadSchemaType.KEYWORD,
    "workspace_id": models.PayloadSchemaType.KEYWORD,
    "document_id": models.PayloadSchemaType.KEYWORD,
    "document_version_id": models.PayloadSchemaType.KEYWORD,
    "evidence_span_id": models.PayloadSchemaType.KEYWORD,
    "is_current_approved": models.PayloadSchemaType.BOOL,
    "projection_revision": models.PayloadSchemaType.INTEGER,
    "page_number": models.PayloadSchemaType.INTEGER,
}
_VECTOR_BLOCKERS = frozenset(
    {
        "AUTHORITY_DENSE_VECTOR_MISMATCH",
        "AUTHORITY_SPARSE_VECTOR_MISMATCH",
    }
)


class AuthorityStorageMismatch(RuntimeError):
    """Existing authority storage cannot be safely repaired in place."""


@dataclass(frozen=True, slots=True)
class AuthorityCollectionSpec:
    generation_id: UUID
    dense_dimension: int

    def __post_init__(self) -> None:
        if self.dense_dimension <= 0:
            raise ValueError("dense dimension must be positive")

    @property
    def physical_collection(self) -> str:
        return f"openrag_authority_v1_{self.generation_id.hex}"

    @property
    def active_alias(self) -> str:
        return _ACTIVE_ALIAS


@dataclass(frozen=True, slots=True)
class AuthorityStorageStatus:
    physical_collection: str
    ready: bool
    blocker_codes: tuple[str, ...]


def _schema_blockers(
    info: models.CollectionInfo,
    spec: AuthorityCollectionSpec,
) -> tuple[str, ...]:
    blockers: list[str] = []
    vectors = info.config.params.vectors
    if (
        not isinstance(vectors, dict)
        or set(vectors) != {"dense"}
        or vectors["dense"].size != spec.dense_dimension
        or vectors["dense"].distance != models.Distance.COSINE
    ):
        blockers.append("AUTHORITY_DENSE_VECTOR_MISMATCH")
    sparse = info.config.params.sparse_vectors
    if (
        not isinstance(sparse, dict)
        or set(sparse) != {"sparse"}
        or sparse["sparse"].modifier != models.Modifier.IDF
    ):
        blockers.append("AUTHORITY_SPARSE_VECTOR_MISMATCH")
    payload = {
        field: index.data_type
        for field, index in info.payload_schema.items()
    }
    if payload != _PAYLOAD_INDEXES:
        blockers.append("AUTHORITY_PAYLOAD_INDEX_MISMATCH")
    if info.status != models.CollectionStatus.GREEN:
        blockers.append("AUTHORITY_COLLECTION_NOT_GREEN")
    return tuple(blockers)


async def probe_authority_storage(
    spec: AuthorityCollectionSpec,
    *,
    client: AsyncQdrantClient | None = None,
) -> AuthorityStorageStatus:
    """Read schema state without creating, repairing, aliasing, or writing points."""

    qdrant = client or get_qdrant()
    try:
        if not await qdrant.collection_exists(spec.physical_collection):
            return AuthorityStorageStatus(
                physical_collection=spec.physical_collection,
                ready=False,
                blocker_codes=("AUTHORITY_COLLECTION_MISSING",),
            )
        info = await qdrant.get_collection(spec.physical_collection)
    except Exception:  # noqa: BLE001 - readiness must fail closed on any upstream error
        return AuthorityStorageStatus(
            physical_collection=spec.physical_collection,
            ready=False,
            blocker_codes=("AUTHORITY_STORAGE_UNAVAILABLE",),
        )
    blockers = _schema_blockers(info, spec)
    return AuthorityStorageStatus(
        physical_collection=spec.physical_collection,
        ready=not blockers,
        blocker_codes=blockers,
    )


async def provision_authority_storage(
    spec: AuthorityCollectionSpec,
    *,
    client: AsyncQdrantClient | None = None,
) -> AuthorityStorageStatus:
    """Create only missing schema; refuse drift and never touch aliases or legacy data."""

    qdrant = client or get_qdrant()
    if not await qdrant.collection_exists(spec.physical_collection):
        try:
            await qdrant.create_collection(
                spec.physical_collection,
                vectors_config={
                    "dense": models.VectorParams(
                        size=spec.dense_dimension,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
                on_disk_payload=True,
            )
        except Exception:
            if not await qdrant.collection_exists(spec.physical_collection):
                raise

    info = await qdrant.get_collection(spec.physical_collection)
    blockers = set(_schema_blockers(info, spec))
    if blockers & _VECTOR_BLOCKERS:
        raise AuthorityStorageMismatch("authority collection schema mismatch")
    current_payload = {
        field: index.data_type
        for field, index in info.payload_schema.items()
    }
    if any(
        field not in _PAYLOAD_INDEXES or _PAYLOAD_INDEXES[field] != data_type
        for field, data_type in current_payload.items()
    ):
        raise AuthorityStorageMismatch("authority collection schema mismatch")
    for field, schema in _PAYLOAD_INDEXES.items():
        if field in current_payload:
            continue
        try:
            await qdrant.create_payload_index(
                spec.physical_collection,
                field_name=field,
                field_schema=schema,
                wait=True,
            )
        except Exception:
            refreshed = await qdrant.get_collection(spec.physical_collection)
            present = refreshed.payload_schema.get(field)
            if present is None or present.data_type != schema:
                raise
    status = await probe_authority_storage(spec, client=qdrant)
    if not status.ready:
        raise AuthorityStorageMismatch("authority collection schema mismatch")
    return status
