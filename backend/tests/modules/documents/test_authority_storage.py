import asyncio
from uuid import uuid4

import pytest
from qdrant_client import models

from openrag.modules.documents.authority_storage import (
    AuthorityCollectionSpec,
    AuthorityStorageMismatch,
    probe_authority_storage,
    provision_authority_storage,
)
from openrag.modules.retrieval.client import COLLECTION, get_qdrant


async def test_provisioning_creates_and_verifies_exact_authority_schema(
    qdrant_collection: None,
) -> None:
    client = get_qdrant()
    spec = AuthorityCollectionSpec(generation_id=uuid4(), dense_dimension=1024)
    legacy_before = (await client.get_collection(COLLECTION)).points_count
    try:
        status = await provision_authority_storage(spec, client=client)
        repeated = await provision_authority_storage(spec, client=client)

        assert status.ready is True
        assert status.blocker_codes == ()
        assert repeated == status
        assert await client.collection_exists(spec.physical_collection)
        info = await client.get_collection(spec.physical_collection)
        assert set(info.payload_schema) == {
            "tenant_id",
            "workspace_id",
            "document_id",
            "document_version_id",
            "evidence_span_id",
            "is_current_approved",
            "projection_revision",
            "page_number",
        }
        assert info.points_count == 0
        assert (await client.get_collection(COLLECTION)).points_count == legacy_before
        aliases = await client.get_aliases()
        assert spec.active_alias not in {alias.alias_name for alias in aliases.aliases}
    finally:
        if await client.collection_exists(spec.physical_collection):
            await client.delete_collection(spec.physical_collection)


async def test_probe_is_read_only_and_reports_missing_storage(
    qdrant_collection: None,
) -> None:
    client = get_qdrant()
    spec = AuthorityCollectionSpec(generation_id=uuid4(), dense_dimension=1024)

    status = await probe_authority_storage(spec, client=client)

    assert status.ready is False
    assert status.blocker_codes == ("AUTHORITY_COLLECTION_MISSING",)
    assert not await client.collection_exists(spec.physical_collection)


async def test_provisioning_refuses_wrong_existing_vector_schema_without_reset(
    qdrant_collection: None,
) -> None:
    client = get_qdrant()
    spec = AuthorityCollectionSpec(generation_id=uuid4(), dense_dimension=1024)
    await client.create_collection(
        spec.physical_collection,
        vectors_config={
            "dense": models.VectorParams(size=8, distance=models.Distance.DOT)
        },
        sparse_vectors_config={},
    )
    try:
        with pytest.raises(AuthorityStorageMismatch, match="schema mismatch"):
            await provision_authority_storage(spec, client=client)

        info = await client.get_collection(spec.physical_collection)
        dense = info.config.params.vectors["dense"]
        assert dense.size == 8
        assert dense.distance == models.Distance.DOT
    finally:
        await client.delete_collection(spec.physical_collection)


async def test_probe_rejects_missing_or_wrong_payload_indexes(
    qdrant_collection: None,
) -> None:
    client = get_qdrant()
    spec = AuthorityCollectionSpec(generation_id=uuid4(), dense_dimension=1024)
    await client.create_collection(
        spec.physical_collection,
        vectors_config={
            "dense": models.VectorParams(size=1024, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    await client.create_payload_index(
        spec.physical_collection,
        field_name="tenant_id",
        field_schema=models.PayloadSchemaType.INTEGER,
        wait=True,
    )
    try:
        status = await probe_authority_storage(spec, client=client)

        assert status.ready is False
        assert status.blocker_codes == ("AUTHORITY_PAYLOAD_INDEX_MISMATCH",)
    finally:
        await client.delete_collection(spec.physical_collection)


async def test_concurrent_provisioners_converge_on_one_verified_schema(
    qdrant_collection: None,
) -> None:
    client = get_qdrant()
    spec = AuthorityCollectionSpec(generation_id=uuid4(), dense_dimension=1024)
    try:
        first, second = await asyncio.gather(
            provision_authority_storage(spec, client=client),
            provision_authority_storage(spec, client=client),
        )

        assert first.ready is True
        assert second.ready is True
        assert await probe_authority_storage(spec, client=client) == first
    finally:
        if await client.collection_exists(spec.physical_collection):
            await client.delete_collection(spec.physical_collection)
