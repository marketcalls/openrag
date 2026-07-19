import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from tests.test_migrations import AUTHORITY_REVISION, authority_db

__all__ = ["authority_db"]


def _seed_legacy_parent(
    engine: Engine,
    ids: SimpleNamespace,
    *,
    answer_status: str | None = None,
    refusal_reason: str | None = None,
) -> tuple[object, object, object]:
    chat_id = uuid4()
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO chats "
                "(id, org_id, workspace_id, user_id, title, updated_at, created_at) "
                "VALUES (:id, :org, :workspace, :user, 'Boundary', now(), now())"
            ),
            {
                "id": chat_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "user": ids.user_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO messages "
                "(id, org_id, workspace_id, chat_id, parent_message_id, sibling_index, "
                "role, content, answer_status, refusal_reason, created_at) VALUES "
                "(:user_message, :org, :workspace, :chat, NULL, 0, 'user', "
                "'question', NULL, NULL, now()), "
                "(:assistant, :org, :workspace, :chat, :user_message, 0, 'assistant', "
                "'legacy answer', :answer_status, :refusal_reason, now())"
            ),
            {
                "user_message": user_message_id,
                "assistant": assistant_message_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "chat": chat_id,
                "answer_status": answer_status,
                "refusal_reason": refusal_reason,
            },
        )
    return chat_id, user_message_id, assistant_message_id


def _insert_legacy_citation(
    connection: sa.Connection,
    ids: SimpleNamespace,
    assistant_message_id: object,
    *,
    document_name: str = "indexed.pdf",
    version_label: str = "Legacy 1",
    section_label: str = "Legacy import",
    section_path: str = '["Legacy import"]',
    locator_kind: str = "page",
    locator_label: str = "7",
    content_hash: str = "legacy-unverified",
    claim_ids: str = "[]",
) -> object:
    citation_id = uuid4()
    document_id = ids.document_ids["indexed"]
    connection.execute(
        text(
            "INSERT INTO citations "
            "(id, org_id, workspace_id, message_id, document_id, document_version_id, "
            "evidence_span_id, chunk_ref, page, score, marker, document_name, version_label, "
            "section_label, section_path, locator_kind, locator_label, content_hash, "
            "claim_ids, verification_state, created_at) VALUES "
            "(:id, :org, :workspace, :message, :document, :document, NULL, "
            "'legacy:7:0', 7, 0.9, 1, :document_name, :version_label, :section_label, "
            "CAST(:section_path AS jsonb), :locator_kind, :locator_label, :content_hash, "
            "CAST(:claim_ids AS jsonb), 'legacy_unverified', now())"
        ),
        {
            "id": citation_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "message": assistant_message_id,
            "document": document_id,
            "document_name": document_name,
            "version_label": version_label,
            "section_label": section_label,
            "section_path": section_path,
            "locator_kind": locator_kind,
            "locator_label": locator_label,
            "content_hash": content_hash,
            "claim_ids": claim_ids,
        },
    )
    return citation_id


def _seed_authority_evidence(
    connection: sa.Connection,
    ids: SimpleNamespace,
    *,
    finalize: bool = True,
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    document_id = ids.document_ids["failed"]
    version_id = uuid4()
    model_id = uuid4()
    policy_id = uuid4()
    chunk_id = uuid4()
    block_id = uuid4()
    link_id = uuid4()
    span_id = uuid4()
    evidence_hash = "b" * 64
    connection.execute(
        text(
            "INSERT INTO models "
            "(id, litellm_model_name, display_name, provider_kind, enabled, sync_status, "
            "supports_chat_completion, supports_structured_json, supports_verifier, "
            "provider_preset_version, created_at) VALUES "
            "(:id, :name, 'Verifier', 'litellm', true, 'ready', true, true, true, "
            "'preset/v1', now())"
        ),
        {"id": model_id, "name": f"verifier/{model_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO grounding_policies "
            "(id, org_id, workspace_id, policy_version, verifier_model_id, "
            "binding_revision, provider_preset_version, credential_fingerprint, "
            "entailment_threshold, calibration_dataset_version, "
            "calibration_dataset_hash, calibration_sample_count, status, created_by, "
            "updated_at, created_at) VALUES "
            "(:id, :org, :workspace, 1, :model, 'binding/v1', 'preset/v1', "
            "'credential-v1', 0.9, 'dataset/v1', :dataset_hash, 1, 'active', :user, "
            "now(), now())"
        ),
        {
            "id": policy_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "model": model_id,
            "dataset_hash": "a" * 64,
            "user": ids.user_id,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_versions "
            "(id, org_id, workspace_id, document_id, sequence, version_label, "
            "version_key, content_hash, source_filename, source_mime, "
            "source_size_bytes, source_storage_key, source_page_count, "
            "parser_profile_version, ocr_profile_version, chunking_profile_version, "
            "embedding_profile_version, index_profile_version, state, provenance_state, "
            "lifecycle_revision, effective_at, expires_at, created_by, updated_at, "
            "created_at) VALUES "
            "(:id, :org, :workspace, :document, 2, 'Approved 2', 'approved 2', :hash, "
            "'approved-2.pdf', 'application/pdf', 10, 'approved-2.pdf', 1, "
            "'parser/v2', 'none/v1', 'chunk/v2', 'embed/v2', 'index/v2', "
            "'processing', 'building', 1, :effective_at, :expires_at, :user, now(), now())"
        ),
        {
            "id": version_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "document": document_id,
            "hash": "c" * 64,
            "effective_at": effective_at,
            "expires_at": expires_at,
            "user": ids.user_id,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_blocks "
            "(id, org_id, document_version_id, parent_block_id, ordinal, text, "
            "page_number, locator_kind, locator_label, block_type, section_path, "
            "source_coordinates, extraction_method, ocr_profile_version, "
            "ocr_confidence, content_hash, created_at) VALUES "
            "(:id, :org, :version, NULL, 0, 'Approved evidence', 1, 'page', '1', "
            "'paragraph', '[\"Safety\"]'::jsonb, NULL, 'native', 'none/v1', NULL, "
            ":hash, now())"
        ),
        {
            "id": block_id,
            "org": ids.org_id,
            "version": version_id,
            "hash": evidence_hash,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_chunks "
            "(id, org_id, document_version_id, ordinal, text, token_count, page_start, "
            "page_end, section_path, content_hash, chunking_profile_version, "
            "embedding_profile_version, created_at) VALUES "
            "(:id, :org, :version, 0, 'Approved evidence', 2, 1, 1, "
            "'[\"Safety\"]'::jsonb, :hash, 'chunk/v2', 'embed/v2', now())"
        ),
        {
            "id": chunk_id,
            "org": ids.org_id,
            "version": version_id,
            "hash": evidence_hash,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_chunk_blocks "
            "(id, org_id, document_version_id, chunk_id, block_id, position, created_at) "
            "VALUES (:id, :org, :version, :chunk, :block, 0, now())"
        ),
        {
            "id": link_id,
            "org": ids.org_id,
            "version": version_id,
            "chunk": chunk_id,
            "block": block_id,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_evidence_spans "
            "(id, org_id, document_version_id, chunk_id, page_number, locator_kind, "
            "locator_label, section_path, content_hash, ordinal, token_count, "
            "artifact_byte_start, artifact_byte_end, created_at) VALUES "
            "(:id, :org, :version, :chunk, 1, 'page', '1', '[\"Safety\"]'::jsonb, "
            ":hash, 0, 2, 0, 17, now())"
        ),
        {
            "id": span_id,
            "org": ids.org_id,
            "version": version_id,
            "chunk": chunk_id,
            "hash": evidence_hash,
        },
    )
    if finalize:
        connection.execute(
            text(
                "UPDATE document_versions SET state='approved', provenance_state='ready' "
                "WHERE id=:id"
            ),
            {"id": version_id},
        )
    return {
        "document_id": document_id,
        "version_id": version_id,
        "model_id": model_id,
        "policy_id": policy_id,
        "chunk_id": chunk_id,
        "block_id": block_id,
        "link_id": link_id,
        "span_id": span_id,
        "content_hash": evidence_hash,
    }


def _insert_authority_citation(
    connection: sa.Connection,
    ids: SimpleNamespace,
    evidence: dict[str, object],
    message_id: object,
    *,
    page: int = 1,
    document_version_id: object | None = None,
    evidence_span_id: object | None = None,
    org_id: object | None = None,
    workspace_id: object | None = None,
    document_id: object | None = None,
    section_label: str = "Safety",
    section_path: str = '["Safety"]',
) -> object:
    citation_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO citations "
            "(id, org_id, workspace_id, message_id, document_id, document_version_id, "
            "evidence_span_id, chunk_ref, page, score, marker, document_name, version_label, "
            "section_label, section_path, locator_kind, locator_label, content_hash, "
            "claim_ids, verification_state, prompt_contract_version, grounding_policy_id, "
            "grounding_policy_version, verifier_model_id, provider_preset_version, "
            "binding_revision, credential_fingerprint, created_at) VALUES "
            "(:id, :org, :workspace, :message, :document, :version, :span, 'span:1', "
            ":page, 0.95, 1, 'failed.pdf', 'Approved 2', :section_label, "
            "CAST(:section_path AS jsonb), 'page', '1', :hash, '[\"claim-1\"]'::jsonb, "
            "'verified', 'grounding/v1', :policy, 1, :model, 'preset/v1', "
            "'binding/v1', 'credential-v1', now())"
        ),
        {
            "id": citation_id,
            "org": org_id or ids.org_id,
            "workspace": workspace_id or ids.workspace_id,
            "message": message_id,
            "document": document_id or evidence["document_id"],
            "version": document_version_id or evidence["version_id"],
            "span": evidence_span_id or evidence["span_id"],
            "page": page,
            "section_label": section_label,
            "section_path": section_path,
            "hash": evidence["content_hash"],
            "policy": evidence["policy_id"],
            "model": evidence["model_id"],
        },
    )
    return citation_id


def _insert_native_version(
    connection: sa.Connection,
    ids: SimpleNamespace,
    *,
    state: str,
    provenance_state: str,
    source_page_count: int | None,
) -> object:
    version_id = uuid4()
    label = f"Native {version_id}"
    connection.execute(
        text(
            "INSERT INTO document_versions "
            "(id, org_id, workspace_id, document_id, sequence, version_label, "
            "version_key, content_hash, source_filename, source_mime, "
            "source_size_bytes, source_storage_key, source_page_count, "
            "parser_profile_version, ocr_profile_version, chunking_profile_version, "
            "embedding_profile_version, index_profile_version, state, provenance_state, "
            "lifecycle_revision, created_by, updated_at, created_at) VALUES "
            "(:id, :org, :workspace, :document, 2, :label, :key, :hash, "
            "'native.pdf', 'application/pdf', 10, :storage, :pages, 'parser/v2', "
            "'none/v1', 'chunk/v2', 'embed/v2', 'index/v2', :state, :provenance, "
            "1, :user, now(), now())"
        ),
        {
            "id": version_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "document": ids.document_ids["failed"],
            "label": label,
            "key": label.casefold(),
            "hash": version_id.hex * 2,
            "storage": f"native/{version_id}.pdf",
            "pages": source_page_count,
            "state": state,
            "provenance": provenance_state,
            "user": ids.user_id,
        },
    )
    return version_id


@pytest.mark.parametrize(
    ("override"),
    [
        {"document_name": ""},
        {"version_label": "Legacy import"},
        {"section_label": ""},
        {"section_path": '["Other"]'},
        {"section_path": "{}"},
        {"section_path": "[]"},
        {"section_path": "[1]"},
        {"section_path": str(["section"] * 9).replace("'", '"')},
        {"section_path": f'["{"s" * 201}"]'},
        {"locator_kind": "section"},
        {"locator_label": "8"},
        {"content_hash": "0" * 64},
        {"claim_ids": '["claim-1"]'},
        {"claim_ids": "{}"},
        {"claim_ids": "[1]"},
        {"claim_ids": str([f"claim-{index}" for index in range(65)]).replace("'", '"')},
    ],
)
def test_legacy_citation_rejects_every_partial_or_hybrid_display_snapshot(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    override: dict[str, str],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            _insert_legacy_citation(connection, ids, assistant, **override)


def test_legacy_citation_workspace_gate_serializes_with_activation(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    activation_started = threading.Event()
    activation_finished = threading.Event()

    connection = engine.connect()
    transaction = connection.begin()
    _insert_legacy_citation(connection, ids, assistant)

    def activate() -> None:
        activation_started.set()
        with engine.begin() as activation_connection:
            activation_connection.execute(
                text(
                    "UPDATE workspaces SET document_authority_enabled=true "
                    "WHERE org_id=:org AND id=:workspace"
                ),
                {"org": ids.org_id, "workspace": ids.workspace_id},
            )
        activation_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(activate)
            assert activation_started.wait(timeout=2)
            # A correct workspace gate holds a conflicting lock until the
            # assistant/citation transaction reaches its commit boundary.
            assert activation_finished.wait(timeout=0.5) is False
            transaction.commit()
            future.result(timeout=2)
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()

    with engine.connect() as check:
        assert (
            check.execute(text("SELECT document_authority_enabled FROM workspaces")).scalar_one()
            is True
        )
        assert check.execute(text("SELECT count(*) FROM citations")).scalar_one() == 1


def test_activation_wins_and_rejects_complete_legacy_transaction(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    chat_id = uuid4()
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO chats "
                "(id, org_id, workspace_id, user_id, title, updated_at, created_at) "
                "VALUES (:id, :org, :workspace, :user, 'Race', now(), now())"
            ),
            {
                "id": chat_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "user": ids.user_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO messages "
                "(id, org_id, workspace_id, chat_id, parent_message_id, sibling_index, "
                "role, content, created_at) VALUES "
                "(:id, :org, :workspace, :chat, NULL, 0, 'user', 'question', now())"
            ),
            {
                "id": user_message_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "chat": chat_id,
            },
        )

    activation_connection = engine.connect()
    activation_transaction = activation_connection.begin()
    activation_connection.execute(
        text("UPDATE workspaces SET document_authority_enabled=true WHERE id=:id"),
        {"id": ids.workspace_id},
    )
    citation_attempted = threading.Event()
    worker_finished = threading.Event()

    def persist_legacy() -> None:
        try:
            with engine.begin() as legacy_connection:
                legacy_connection.execute(
                    text(
                        "INSERT INTO messages "
                        "(id, org_id, workspace_id, chat_id, parent_message_id, "
                        "sibling_index, role, content, created_at) VALUES "
                        "(:id, :org, :workspace, :chat, :parent, 0, 'assistant', "
                        "'must rollback', now())"
                    ),
                    {
                        "id": assistant_message_id,
                        "org": ids.org_id,
                        "workspace": ids.workspace_id,
                        "chat": chat_id,
                        "parent": user_message_id,
                    },
                )
                citation_attempted.set()
                _insert_legacy_citation(legacy_connection, ids, assistant_message_id)
        finally:
            worker_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(persist_legacy)
            assert citation_attempted.wait(timeout=2)
            assert worker_finished.wait(timeout=0.5) is False
            activation_transaction.commit()
            with pytest.raises(sa.exc.DBAPIError):
                future.result(timeout=2)
    finally:
        if activation_transaction.is_active:
            activation_transaction.rollback()
        activation_connection.close()

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM messages WHERE id=:id"),
                {"id": assistant_message_id},
            ).scalar_one()
            == 0
        )
        assert connection.execute(text("SELECT count(*) FROM citations")).scalar_one() == 0


def test_legacy_citation_never_satisfies_grounded_cardinality_and_is_immutable(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with engine.begin() as connection:
        citation_id = _insert_legacy_citation(connection, ids, assistant)

    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
                {"id": assistant},
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE workspaces SET document_authority_enabled=true WHERE id=:id"),
            {"id": ids.workspace_id},
        )
    with pytest.raises(sa.exc.DBAPIError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE citations SET score=0.1 WHERE id=:id"),
                {"id": citation_id},
            )
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT m.answer_status, c.verification_state "
                    "FROM messages m JOIN citations c ON c.message_id=m.id "
                    "WHERE m.id=:id"
                ),
                {"id": assistant},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "answer_status": None,
        "verification_state": "legacy_unverified",
    }


def test_version_identity_is_immutable_and_only_one_current_approval_exists(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    indexed_id = ids.document_ids["indexed"]
    with pytest.raises(sa.exc.DBAPIError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE document_versions SET version_label='changed' WHERE id=:id"),
                {"id": indexed_id},
            )

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, org_id, workspace_id, document_id, sequence, version_label, "
                    "version_key, content_hash, source_filename, source_mime, "
                    "source_size_bytes, source_storage_key, source_page_count, "
                    "parser_profile_version, ocr_profile_version, "
                    "chunking_profile_version, embedding_profile_version, "
                    "index_profile_version, state, provenance_state, lifecycle_revision, "
                    "created_by, updated_at, created_at) VALUES "
                    "(:id, :org, :workspace, :document, 2, 'Approved 2', 'approved 2', "
                    ":hash, 'approved-2.pdf', 'application/pdf', 10, 'approved-2.pdf', 1, "
                    "'parser/v2', 'none/v1', 'chunk/v2', 'embed/v2', 'index/v2', "
                    "'approved', 'ready', 1, :user, now(), now())"
                ),
                {
                    "id": uuid4(),
                    "org": ids.org_id,
                    "workspace": ids.workspace_id,
                    "document": indexed_id,
                    "hash": "f" * 64,
                    "user": ids.user_id,
                },
            )


@pytest.mark.parametrize(
    ("state", "provenance_state", "allowed"),
    [
        ("draft", "none", True),
        ("processing", "none", True),
        ("processing", "building", True),
        ("failed", "none", True),
        ("failed", "failed", True),
        ("draft", "ready", False),
        ("review", "building", False),
        ("approved", "ready", False),
        ("rejected", "none", False),
        ("superseded", "ready", False),
        ("obsolete", "ready", False),
    ],
)
def test_native_page_count_nullability_follows_version_lifecycle(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    state: str,
    provenance_state: str,
    allowed: bool,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    if allowed:
        with engine.begin() as connection:
            version_id = _insert_native_version(
                connection,
                ids,
                state=state,
                provenance_state=provenance_state,
                source_page_count=None,
            )
        assert version_id is not None
    else:
        with pytest.raises(sa.exc.IntegrityError, match="page_count"):
            with engine.begin() as connection:
                _insert_native_version(
                    connection,
                    ids,
                    state=state,
                    provenance_state=provenance_state,
                    source_page_count=None,
                )


def test_page_count_populates_once_during_build_then_freezes_when_ready(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        version_id = _insert_native_version(
            connection,
            ids,
            state="processing",
            provenance_state="building",
            source_page_count=None,
        )
        connection.execute(
            text("UPDATE document_versions SET source_page_count=3 WHERE id=:id"),
            {"id": version_id},
        )

    for pages in (None, 4):
        with pytest.raises(sa.exc.DBAPIError, match="page count"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE document_versions SET source_page_count=:pages WHERE id=:id"
                    ),
                    {"id": version_id, "pages": pages},
                )

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE document_versions SET provenance_state='ready' WHERE id=:id"),
            {"id": version_id},
        )
    with pytest.raises(sa.exc.DBAPIError, match="page count"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE document_versions SET source_page_count=5 WHERE id=:id"),
                {"id": version_id},
            )


def test_page_count_cannot_be_populated_in_same_update_that_becomes_ready(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        version_id = _insert_native_version(
            connection,
            ids,
            state="processing",
            provenance_state="building",
            source_page_count=None,
        )
    with pytest.raises(sa.exc.DBAPIError, match="page count"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_versions SET source_page_count=3, "
                    "provenance_state='ready' WHERE id=:id"
                ),
                {"id": version_id},
            )


def test_legacy_pending_page_count_can_be_backfilled_once_without_fabrication(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    indexed_id = ids.document_ids["indexed"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET page_count=NULL WHERE id=:id"),
            {"id": indexed_id},
        )
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT source_page_count FROM document_versions WHERE id=:id"),
            {"id": indexed_id},
        ).scalar_one() is None
        connection.execute(
            text("UPDATE document_versions SET source_page_count=7 WHERE id=:id"),
            {"id": indexed_id},
        )

    for pages in (None, 8):
        with pytest.raises(sa.exc.DBAPIError, match="page count"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE document_versions SET source_page_count=:pages WHERE id=:id"
                    ),
                    {"id": indexed_id, "pages": pages},
                )


def _insert_version_decision(
    connection: sa.Connection,
    ids: SimpleNamespace,
    *,
    decision_id: object | None = None,
    document_id: object | None = None,
    version_id: object | None = None,
    lifecycle_revision: int = 1,
    decision: str = "approved",
    actor_id: object | None = None,
    reason: str | None = "meets policy",
) -> object:
    record_id = decision_id or uuid4()
    target_document = document_id or ids.document_ids["indexed"]
    connection.execute(
        text(
            "INSERT INTO document_version_decision_records "
            "(id, org_id, workspace_id, document_id, document_version_id, "
            "lifecycle_revision, decision, actor_id, reason, created_at) VALUES "
            "(:id, :org, :workspace, :document, :version, :revision, :decision, "
            ":actor, :reason, now())"
        ),
        {
            "id": record_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "document": target_document,
            "version": version_id or target_document,
            "revision": lifecycle_revision,
            "decision": decision,
            "actor": actor_id or ids.user_id,
            "reason": reason,
        },
    )
    return record_id


def test_version_decision_record_is_append_only_and_preserves_bounded_reason(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        record_id = _insert_version_decision(connection, ids, reason="  meets policy  ")
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT decision, reason FROM document_version_decision_records "
                "WHERE id=:id"
            ),
            {"id": record_id},
        ).one() == ("approved", "  meets policy  ")

    for mutation in (
        "UPDATE document_version_decision_records SET reason='rewritten' WHERE id=:id",
        "DELETE FROM document_version_decision_records WHERE id=:id",
    ):
        with pytest.raises(sa.exc.DBAPIError, match="decision record"):
            with engine.begin() as connection:
                connection.execute(text(mutation), {"id": record_id})


@pytest.mark.parametrize(
    "override",
    [
        {"decision": "reviewed"},
        {"lifecycle_revision": 0},
        {"reason": ""},
        {"reason": "   "},
        {"reason": "x" * 501},
    ],
)
def test_version_decision_record_rejects_invalid_bounded_values(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    override: dict[str, object],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            _insert_version_decision(connection, ids, **override)


def test_version_decision_record_is_exactly_scoped_and_unique_per_revision(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        _insert_version_decision(connection, ids)

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            _insert_version_decision(connection, ids, reason="duplicate decision")

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            _insert_version_decision(
                connection,
                ids,
                document_id=ids.document_ids["indexed"],
                version_id=ids.document_ids["failed"],
                lifecycle_revision=2,
            )


def test_version_decision_actor_must_belong_to_the_same_organization(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    other_org_id = uuid4()
    other_user_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at) "
                "VALUES (:id, 'Peer organization', now())"
            ),
            {"id": other_org_id},
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, org_id, email, password_hash, active, is_platform_superadmin, "
                "created_at) VALUES "
                "(:id, :org, 'peer-decision@example.com', 'inert', true, false, now())"
            ),
            {"id": other_user_id, "org": other_org_id},
        )

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            _insert_version_decision(
                connection,
                ids,
                actor_id=other_user_id,
            )


def test_decision_history_restricts_rejected_version_metadata_deletion(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        version_id = _insert_native_version(
            connection,
            ids,
            state="rejected",
            provenance_state="none",
            source_page_count=1,
        )
        _insert_version_decision(
            connection,
            ids,
            document_id=ids.document_ids["failed"],
            version_id=version_id,
            decision="rejected",
            reason="insufficient provenance",
        )

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM document_versions WHERE id=:id"),
                {"id": version_id},
            )


def test_downgrade_refuses_to_discard_document_version_decision_history(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        record_id = _insert_version_decision(connection, ids)

    with pytest.raises(RuntimeError, match="document version decision record"):
        command.downgrade(config, "6c4a2f8b9d10")

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM document_version_decision_records WHERE id=:id"),
            {"id": record_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == AUTHORITY_REVISION


def test_authority_citation_requires_exact_evidence_and_last_delete_is_deferred(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)
        connection.execute(
            text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
            {"id": assistant},
        )
        citation_id = _insert_authority_citation(connection, ids, evidence, assistant)

    with pytest.raises(sa.exc.DBAPIError, match="authority evidence"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM citations WHERE id=:id"),
                {"id": citation_id},
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM messages WHERE id=:id"), {"id": assistant})
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM citations WHERE id=:id"),
                {"id": citation_id},
            ).scalar_one()
            == 0
        )


def test_authority_citation_section_label_is_derived_from_evidence_path(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with pytest.raises(sa.exc.DBAPIError, match="immutable evidence"):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids)
            connection.execute(
                text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
                {"id": assistant},
            )
            _insert_authority_citation(
                connection,
                ids,
                evidence,
                assistant,
                section_label="FORGED SECTION",
            )


@pytest.mark.parametrize("parent_kind", ["user", "historical", "refused"])
def test_authority_citation_rejects_every_ineligible_parent_state(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    parent_kind: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, user_message, assistant = _seed_legacy_parent(
        engine,
        ids,
        answer_status="refused" if parent_kind == "refused" else None,
        refusal_reason="below_threshold" if parent_kind == "refused" else None,
    )
    parent_id = user_message if parent_kind == "user" else assistant
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids)
            _insert_authority_citation(connection, ids, evidence, parent_id)


@pytest.mark.parametrize("parent_kind", ["user", "refused", "grounded"])
def test_legacy_citation_rejects_every_nonhistorical_parent_state(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    parent_kind: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, user_message, assistant = _seed_legacy_parent(
        engine,
        ids,
        answer_status="refused" if parent_kind == "refused" else None,
        refusal_reason="below_threshold" if parent_kind == "refused" else None,
    )
    parent_id = user_message if parent_kind == "user" else assistant
    if parent_kind == "grounded":
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids)
            connection.execute(
                text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
                {"id": assistant},
            )
            _insert_authority_citation(connection, ids, evidence, assistant)
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            _insert_legacy_citation(connection, ids, parent_id)


@pytest.mark.parametrize(
    ("answer_status", "refusal_reason"),
    [(None, None), ("refused", "below_threshold")],
)
def test_authority_parent_cannot_leave_grounded_state_while_citation_survives(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    answer_status: str | None,
    refusal_reason: str | None,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)
        connection.execute(
            text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
            {"id": assistant},
        )
        _insert_authority_citation(connection, ids, evidence, assistant)

    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE messages SET answer_status=:answer_status, "
                    "refusal_reason=:refusal_reason WHERE id=:id"
                ),
                {
                    "answer_status": answer_status,
                    "refusal_reason": refusal_reason,
                    "id": assistant,
                },
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_deleting_chat_cascades_grounded_message_and_authority_citation(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    chat_id, _user, assistant = _seed_legacy_parent(engine, ids)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)
        connection.execute(
            text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
            {"id": assistant},
        )
        citation_id = _insert_authority_citation(connection, ids, evidence, assistant)

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM chats WHERE id=:id"), {"id": chat_id})
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM citations WHERE id=:id"),
                {"id": citation_id},
            ).scalar_one()
            == 0
        )


def _insert_readiness_row(
    connection: sa.Connection,
    ids: SimpleNamespace,
    evidence: dict[str, object],
    *,
    status: str = "building",
    expires_at: datetime | None = None,
    include_result: bool = False,
    checked_at: datetime | None = None,
    activated_at: datetime | None = None,
    activated_by: object | None = None,
) -> object:
    readiness_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO document_authority_readiness "
            "(id, generation_id, org_id, workspace_id, request_digest, "
            "physical_collection, collection_alias, schema_version, "
            "current_version_count, ready_version_count, projected_version_count, "
            "point_count, payload_index_digest, provenance_digest, "
            "lifecycle_revision_digest, grounding_policy_id, grounding_policy_version, "
            "calibration_hash, verifier_model_id, provider_preset_version, "
            "binding_revision, credential_fingerprint, readiness_digest, signature, "
            "blocker_codes, status, attempts, checked_at, expires_at, activated_at, "
            "activated_by, created_at) VALUES "
            "(:id, :generation, :org, :workspace, :request, 'authority-v2', "
            "'authority-current', 1, 0, 0, 0, 0, :payload, :provenance, :lifecycle, "
            ":policy, 1, :calibration, :model, 'preset/v1', 'binding/v1', "
            "'credential-v1', :readiness, :signature, ARRAY[]::varchar[], :status, 0, "
            ":checked_at, :expires_at, :activated_at, :activated_by, now())"
        ),
        {
            "id": readiness_id,
            "generation": uuid4(),
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "request": "1" * 64,
            "payload": "2" * 64 if include_result else None,
            "provenance": "3" * 64 if include_result else None,
            "lifecycle": "4" * 64 if include_result else None,
            "policy": evidence["policy_id"],
            "calibration": "a" * 64,
            "model": evidence["model_id"],
            "readiness": "5" * 64 if include_result else None,
            "signature": "6" * 64 if include_result else None,
            "status": status,
            "checked_at": checked_at,
            "expires_at": expires_at
            or datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            "activated_at": activated_at,
            "activated_by": activated_by,
        },
    )
    return readiness_id


@pytest.mark.parametrize("status", ["passed", "stale", "failed", "activated"])
def test_readiness_rejects_direct_nonbuilding_insert(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    status: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with pytest.raises(sa.exc.DBAPIError, match="readiness"):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids, finalize=False)
            _insert_readiness_row(
                connection,
                ids,
                evidence,
                status=status,
                include_result=status in {"passed", "activated"},
                checked_at=(
                    datetime.now(UTC).replace(tzinfo=None)
                    if status in {"passed", "activated"}
                    else None
                ),
                activated_at=(
                    datetime.now(UTC).replace(tzinfo=None)
                    if status == "activated"
                    else None
                ),
                activated_by=ids.user_id if status == "activated" else None,
            )


@pytest.mark.parametrize(
    "invalid_shape",
    ["result", "checked", "activated_at", "activated_by"],
)
def test_building_readiness_insert_rejects_result_or_activation_state(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    invalid_shape: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with pytest.raises(sa.exc.DBAPIError, match="readiness"):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids, finalize=False)
            _insert_readiness_row(
                connection,
                ids,
                evidence,
                include_result=invalid_shape == "result",
                checked_at=(
                    datetime.now(UTC).replace(tzinfo=None)
                    if invalid_shape == "checked"
                    else None
                ),
                activated_at=(
                    datetime.now(UTC).replace(tzinfo=None)
                    if invalid_shape == "activated_at"
                    else None
                ),
                activated_by=ids.user_id if invalid_shape == "activated_by" else None,
            )


def test_readiness_generation_preserves_signed_tenant_snapshot_and_is_terminal(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    readiness_id = uuid4()
    generation_id = uuid4()
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)
        connection.execute(
            text(
                "INSERT INTO document_authority_readiness "
                "(id, generation_id, org_id, workspace_id, request_digest, "
                "physical_collection, collection_alias, schema_version, "
                "current_version_count, ready_version_count, projected_version_count, "
                "point_count, grounding_policy_id, grounding_policy_version, "
                "calibration_hash, verifier_model_id, provider_preset_version, "
                "binding_revision, credential_fingerprint, readiness_digest, signature, "
                "blocker_codes, status, attempts, expires_at, created_at) VALUES "
                "(:id, :generation, :org, :workspace, :request_digest, 'authority-v1', "
                "'authority-current', 1, 1, 1, 1, 1, :policy, 1, :calibration_hash, "
                ":model, 'preset/v1', 'binding/v1', 'credential-v1', "
                "NULL, NULL, ARRAY[]::varchar[], 'building', 1, "
                "now() + interval '1 hour', now())"
            ),
            {
                "id": readiness_id,
                "generation": generation_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "request_digest": "d" * 64,
                "policy": evidence["policy_id"],
                "calibration_hash": "a" * 64,
                "model": evidence["model_id"],
            },
        )
        connection.execute(
            text(
                "UPDATE document_authority_readiness SET status='failed', "
                "readiness_digest=:readiness, signature=:signature WHERE id=:id"
            ),
            {
                "id": readiness_id,
                "readiness": "e" * 64,
                "signature": "f" * 64,
            },
        )
        # Keep only the policy snapshot needed by readiness so downgrade reaches
        # the readiness-specific fail-closed preflight branch.
        connection.execute(
            text("DELETE FROM document_evidence_spans WHERE id=:id"),
            {"id": evidence["span_id"]},
        )
        connection.execute(
            text("DELETE FROM document_chunks WHERE document_version_id=:id"),
            {"id": evidence["version_id"]},
        )
        connection.execute(
            text("DELETE FROM document_versions WHERE id=:id"),
            {"id": evidence["version_id"]},
        )

    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT org_id, workspace_id, generation_id, readiness_digest, signature "
                    "FROM document_authority_readiness WHERE id=:id"
                ),
                {"id": readiness_id},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "org_id": ids.org_id,
        "workspace_id": ids.workspace_id,
        "generation_id": generation_id,
        "readiness_digest": "e" * 64,
        "signature": "f" * 64,
    }

    with pytest.raises(sa.exc.DBAPIError, match="terminal readiness"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE document_authority_readiness SET attempts=2 WHERE id=:id"),
                {"id": readiness_id},
            )

    with pytest.raises(RuntimeError, match="readiness generation"):
        command.downgrade(config, "6c4a2f8b9d10")


@pytest.mark.parametrize(
    ("readiness_terminal", "calibration_terminal"),
    [("stale", "passed"), ("activated", "failed")],
)
def test_readiness_and_calibration_allow_normal_transitions_then_freeze_terminals(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    readiness_terminal: str,
    calibration_terminal: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    readiness_id = uuid4()
    calibration_id = uuid4()
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)
        connection.execute(
            text(
                "INSERT INTO document_authority_readiness "
                "(id, generation_id, org_id, workspace_id, request_digest, "
                "physical_collection, collection_alias, schema_version, "
                "current_version_count, ready_version_count, projected_version_count, "
                "point_count, grounding_policy_id, grounding_policy_version, "
                "calibration_hash, verifier_model_id, provider_preset_version, "
                "binding_revision, credential_fingerprint, blocker_codes, status, "
                "attempts, expires_at, created_at) VALUES "
                "(:id, :generation, :org, :workspace, :digest, 'authority-v2', "
                "'authority-current', 1, 0, 0, 0, 0, :policy, 1, :calibration, "
                ":model, 'preset/v1', 'binding/v1', 'credential-v1', "
                "ARRAY[]::varchar[], 'building', 0, now() + interval '1 hour', now())"
            ),
            {
                "id": readiness_id,
                "generation": uuid4(),
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "digest": "1" * 64,
                "policy": evidence["policy_id"],
                "calibration": "a" * 64,
                "model": evidence["model_id"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO grounding_calibration_runs "
                "(id, generation_id, org_id, workspace_id, policy_id, "
                "idempotency_digest, requested_binding_revision, "
                "requested_preset_version, requested_credential_fingerprint, state, "
                "attempts, sample_count, supported_count, refused_count, updated_at, "
                "created_at) VALUES (:id, :generation, :org, :workspace, :policy, "
                ":digest, 'binding/v1', 'preset/v1', 'credential-v1', 'queued', "
                "0, 0, 0, 0, now(), now())"
            ),
            {
                "id": calibration_id,
                "generation": uuid4(),
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "policy": evidence["policy_id"],
                "digest": "2" * 64,
            },
        )

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_authority_readiness SET status='passed', "
                "payload_index_digest=:payload, provenance_digest=:provenance, "
                "lifecycle_revision_digest=:lifecycle, readiness_digest=:readiness, "
                "signature=:signature, checked_at=now() WHERE id=:id"
            ),
            {
                "id": readiness_id,
                "payload": "3" * 64,
                "provenance": "4" * 64,
                "lifecycle": "5" * 64,
                "readiness": "6" * 64,
                "signature": "7" * 64,
            },
        )
        if readiness_terminal == "activated":
            connection.execute(
                text(
                    "UPDATE document_authority_readiness SET status='activated', "
                    "activated_at=now(), activated_by=:actor WHERE id=:id"
                ),
                {"id": readiness_id, "actor": ids.user_id},
            )
        else:
            connection.execute(
                text("UPDATE document_authority_readiness SET status='stale' WHERE id=:id"),
                {"id": readiness_id},
            )
        connection.execute(
            text("UPDATE grounding_calibration_runs SET state='running' WHERE id=:id"),
            {"id": calibration_id},
        )
        connection.execute(
            text("UPDATE grounding_calibration_runs SET state=:state WHERE id=:id"),
            {"id": calibration_id, "state": calibration_terminal},
        )

    with pytest.raises(sa.exc.DBAPIError, match="terminal readiness"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE document_authority_readiness SET attempts=1 WHERE id=:id"),
                {"id": readiness_id},
            )
    with pytest.raises(sa.exc.DBAPIError, match="terminal calibration"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE grounding_calibration_runs SET attempts=1 WHERE id=:id"),
                {"id": calibration_id},
            )


def test_readiness_rejects_direct_activation_and_freezes_passed_snapshot(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    readiness_id = uuid4()
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)
        connection.execute(
            text(
                "INSERT INTO document_authority_readiness "
                "(id, generation_id, org_id, workspace_id, request_digest, "
                "physical_collection, collection_alias, schema_version, "
                "current_version_count, ready_version_count, projected_version_count, "
                "point_count, grounding_policy_id, grounding_policy_version, "
                "calibration_hash, verifier_model_id, provider_preset_version, "
                "binding_revision, credential_fingerprint, blocker_codes, status, "
                "attempts, expires_at, created_at) VALUES "
                "(:id, :generation, :org, :workspace, :digest, 'authority-v2', "
                "'authority-current', 1, 0, 0, 0, 0, :policy, 1, :calibration, "
                ":model, 'preset/v1', 'binding/v1', 'credential-v1', "
                "ARRAY[]::varchar[], 'building', 0, now() + interval '1 hour', now())"
            ),
            {
                "id": readiness_id,
                "generation": uuid4(),
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "digest": "1" * 64,
                "policy": evidence["policy_id"],
                "calibration": "a" * 64,
                "model": evidence["model_id"],
            },
        )

    with pytest.raises(sa.exc.DBAPIError, match="readiness transition"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_authority_readiness SET status='activated', "
                    "activated_at=now(), activated_by=:actor WHERE id=:id"
                ),
                {"id": readiness_id, "actor": ids.user_id},
            )

    with pytest.raises(sa.exc.DBAPIError, match="complete signed result"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE document_authority_readiness SET status='passed' WHERE id=:id"),
                {"id": readiness_id},
            )

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_authority_readiness SET status='passed', attempts=1, "
                "payload_index_digest=:payload, provenance_digest=:provenance, "
                "lifecycle_revision_digest=:lifecycle, readiness_digest=:readiness, "
                "signature=:signature, checked_at=now() WHERE id=:id"
            ),
            {
                "id": readiness_id,
                "payload": "3" * 64,
                "provenance": "4" * 64,
                "lifecycle": "5" * 64,
                "readiness": "6" * 64,
                "signature": "7" * 64,
            },
        )

    for mutation in (
        "UPDATE document_authority_readiness SET signature=repeat('8',64) WHERE id=:id",
        "UPDATE document_authority_readiness SET readiness_digest=repeat('9',64) WHERE id=:id",
        "UPDATE document_authority_readiness SET request_digest=repeat('a',64) WHERE id=:id",
        "UPDATE document_authority_readiness SET attempts=attempts+1 WHERE id=:id",
        "UPDATE document_authority_readiness SET status='failed' WHERE id=:id",
    ):
        with pytest.raises(sa.exc.DBAPIError, match="readiness"):
            with engine.begin() as connection:
                connection.execute(text(mutation), {"id": readiness_id})


def test_readiness_pass_and_activation_require_fresh_unactivated_generation(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)
        expired_id = _insert_readiness_row(
            connection,
            ids,
            evidence,
            expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
        )
        preactivated_id = _insert_readiness_row(connection, ids, evidence)
        expiring_id = _insert_readiness_row(
            connection,
            ids,
            evidence,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=2),
        )

    with pytest.raises(sa.exc.DBAPIError, match="unexpired"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_authority_readiness SET status='passed', "
                    "payload_index_digest=repeat('2',64), "
                    "provenance_digest=repeat('3',64), "
                    "lifecycle_revision_digest=repeat('4',64), "
                    "readiness_digest=repeat('5',64), signature=repeat('6',64), "
                    "checked_at=now() WHERE id=:id"
                ),
                {"id": expired_id},
            )

    with pytest.raises(sa.exc.DBAPIError, match="activation fields"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_authority_readiness SET activated_at=now(), "
                    "activated_by=:actor WHERE id=:id"
                ),
                {"id": preactivated_id, "actor": ids.user_id},
            )

    with pytest.raises(sa.exc.DBAPIError, match="activation fields"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_authority_readiness SET status='passed', "
                    "payload_index_digest=repeat('2',64), "
                    "provenance_digest=repeat('3',64), "
                    "lifecycle_revision_digest=repeat('4',64), "
                    "readiness_digest=repeat('5',64), signature=repeat('6',64), "
                    "checked_at=now(), activated_at=now(), activated_by=:actor "
                    "WHERE id=:id"
                ),
                {"id": preactivated_id, "actor": ids.user_id},
            )

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_authority_readiness SET lease_owner='worker-1', "
                "lease_expires_at=now() + interval '30 seconds', attempts=1 "
                "WHERE id=:id"
            ),
            {"id": expiring_id},
        )
        connection.execute(
            text(
                "UPDATE document_authority_readiness SET status='passed', "
                "payload_index_digest=repeat('2',64), "
                "provenance_digest=repeat('3',64), "
                "lifecycle_revision_digest=repeat('4',64), "
                "readiness_digest=repeat('5',64), signature=repeat('6',64), "
                "checked_at=now() WHERE id=:id"
            ),
            {"id": expiring_id},
        )

    assert threading.Event().wait(timeout=2.1) is False
    with pytest.raises(sa.exc.DBAPIError, match="unexpired"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_authority_readiness SET status='activated', "
                    "activated_at=now(), activated_by=:actor WHERE id=:id"
                ),
                {"id": expiring_id, "actor": ids.user_id},
            )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE document_evidence_spans SET content_hash=repeat('3',64) WHERE id=:span",
        "UPDATE document_evidence_spans SET page_number=2 WHERE id=:span",
        "UPDATE document_evidence_spans SET locator_kind='section' WHERE id=:span",
        "UPDATE document_evidence_spans SET locator_label='Safety.1' WHERE id=:span",
        "UPDATE document_evidence_spans SET section_path='[\"Other\"]'::jsonb WHERE id=:span",
        "UPDATE document_evidence_spans SET ordinal=1 WHERE id=:span",
        "UPDATE document_evidence_spans SET token_count=3 WHERE id=:span",
        "UPDATE document_evidence_spans SET artifact_byte_start=1 WHERE id=:span",
        "UPDATE document_evidence_spans SET artifact_byte_end=16 WHERE id=:span",
        "UPDATE document_evidence_spans SET document_version_id=:other WHERE id=:span",
        "UPDATE document_evidence_spans SET chunk_id=:other WHERE id=:span",
        "UPDATE document_chunks SET text='mutated' WHERE document_version_id=:version",
    ],
)
def test_referenced_evidence_artifacts_are_immutable(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    mutation: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)
        connection.execute(
            text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
            {"id": assistant},
        )
        _insert_authority_citation(connection, ids, evidence, assistant)

    with pytest.raises(sa.exc.DBAPIError, match="evidence artifact"):
        with engine.begin() as connection:
            connection.execute(
                text(mutation),
                {
                    "span": evidence["span_id"],
                    "version": evidence["version_id"],
                    "other": ids.document_ids["indexed"],
                },
            )


def test_processing_or_building_provenance_uses_delete_reinsert_and_fk_cascades(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)

    with pytest.raises(sa.exc.DBAPIError, match="evidence artifact"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE document_blocks SET text='rewrite' WHERE id=:id"),
                {"id": evidence["block_id"]},
            )

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM document_chunks WHERE id=:id"),
            {"id": evidence["chunk_id"]},
        )
        assert connection.execute(
            text(
                "SELECT count(*) FROM document_evidence_spans WHERE id=:span "
                "UNION ALL SELECT count(*) FROM document_chunk_blocks WHERE id=:link"
            ),
            {"span": evidence["span_id"], "link": evidence["link_id"]},
        ).scalars().all() == [0, 0]
        connection.execute(
            text("DELETE FROM document_blocks WHERE id=:id"),
            {"id": evidence["block_id"]},
        )
        connection.execute(
            text(
                "INSERT INTO document_blocks "
                "(id, org_id, document_version_id, parent_block_id, ordinal, text, "
                "page_number, locator_kind, locator_label, block_type, section_path, "
                "source_coordinates, extraction_method, ocr_profile_version, "
                "ocr_confidence, content_hash, created_at) VALUES "
                "(:id, :org, :version, NULL, 0, 'rebuilt', 1, 'page', '1', "
                "'paragraph', '[\"Safety\"]'::jsonb, NULL, 'native', 'none/v1', "
                "NULL, :hash, now())"
            ),
            {
                "id": evidence["block_id"],
                "org": ids.org_id,
                "version": evidence["version_id"],
                "hash": evidence["content_hash"],
            },
        )
        connection.execute(
            text(
                "UPDATE document_versions SET state='failed', "
                "provenance_state='failed' WHERE id=:id"
            ),
            {"id": evidence["version_id"]},
        )
        connection.execute(
            text("DELETE FROM document_versions WHERE id=:id"),
            {"id": evidence["version_id"]},
        )
        assert connection.execute(
            text(
                "SELECT count(*) FROM document_blocks WHERE document_version_id=:id "
                "UNION ALL SELECT count(*) FROM document_chunks "
                "WHERE document_version_id=:id"
            ),
            {"id": evidence["version_id"]},
        ).scalars().all() == [0, 0]


def test_provenance_mutation_serializes_with_ready_transition(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)

    mutation_connection = engine.connect()
    mutation_transaction = mutation_connection.begin()
    mutation_connection.execute(
        text("DELETE FROM document_blocks WHERE id=:id"),
        {"id": evidence["block_id"]},
    )
    transition_started = threading.Event()
    transition_finished = threading.Event()

    def mark_ready() -> None:
        transition_started.set()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_versions SET state='approved', "
                    "provenance_state='ready' WHERE id=:id"
                ),
                {"id": evidence["version_id"]},
            )
        transition_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mark_ready)
            assert transition_started.wait(timeout=2)
            assert transition_finished.wait(timeout=0.5) is False
            mutation_transaction.commit()
            future.result(timeout=2)
    finally:
        if mutation_transaction.is_active:
            mutation_transaction.rollback()
        mutation_connection.close()

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT state, provenance_state FROM document_versions WHERE id=:id"
            ),
            {"id": evidence["version_id"]},
        ).one()
    assert tuple(row) == ("approved", "ready")


@pytest.mark.parametrize(
    ("state", "provenance_state"),
    [
        ("processing", "building"),
        ("processing", "none"),
        ("approved", "building"),
    ],
)
def test_nonready_rebuild_shapes_allow_artifact_delete_reinsert(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    state: str,
    provenance_state: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)
        connection.execute(
            text(
                "UPDATE document_versions SET state=:state, "
                "provenance_state=:provenance WHERE id=:id"
            ),
            {
                "id": evidence["version_id"],
                "state": state,
                "provenance": provenance_state,
            },
        )
        connection.execute(
            text("DELETE FROM document_blocks WHERE id=:id"),
            {"id": evidence["block_id"]},
        )
        connection.execute(
            text(
                "INSERT INTO document_blocks "
                "(id, org_id, document_version_id, ordinal, text, page_number, "
                "locator_kind, locator_label, block_type, section_path, "
                "extraction_method, ocr_profile_version, content_hash, created_at) "
                "VALUES (:id, :org, :version, 0, 'rebuilt', 1, 'page', '1', "
                "'paragraph', '[\"Safety\"]'::jsonb, 'native', 'none/v1', "
                ":hash, now())"
            ),
            {
                "id": evidence["block_id"],
                "org": ids.org_id,
                "version": evidence["version_id"],
                "hash": evidence["content_hash"],
            },
        )


@pytest.mark.parametrize(
    ("state", "provenance_state"),
    [
        ("processing", "ready"),
        ("approved", "ready"),
        ("failed", "failed"),
    ],
)
@pytest.mark.parametrize("operation", ["insert", "delete"])
def test_artifact_mutation_rejects_ready_or_nonrebuild_owner_shapes(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    state: str,
    provenance_state: str,
    operation: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)
        connection.execute(
            text(
                "UPDATE document_versions SET state=:state, "
                "provenance_state=:provenance WHERE id=:id"
            ),
            {
                "id": evidence["version_id"],
                "state": state,
                "provenance": provenance_state,
            },
        )

    with pytest.raises(sa.exc.DBAPIError, match="evidence artifact"):
        with engine.begin() as connection:
            if operation == "delete":
                connection.execute(
                    text("DELETE FROM document_blocks WHERE id=:id"),
                    {"id": evidence["block_id"]},
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO document_blocks "
                        "(id, org_id, document_version_id, ordinal, text, page_number, "
                        "locator_kind, locator_label, block_type, section_path, "
                        "extraction_method, ocr_profile_version, content_hash, created_at) "
                        "VALUES (:id, :org, :version, 1, 'late', 1, 'page', '1', "
                        "'paragraph', '[\"Safety\"]'::jsonb, 'native', 'none/v1', "
                        ":hash, now())"
                    ),
                    {
                        "id": uuid4(),
                        "org": ids.org_id,
                        "version": evidence["version_id"],
                        "hash": "9" * 64,
                    },
                )


def test_processing_ready_version_rejects_parent_cascade_delete(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids, finalize=False)
        connection.execute(
            text(
                "UPDATE document_versions SET provenance_state='ready' WHERE id=:id"
            ),
            {"id": evidence["version_id"]},
        )

    with pytest.raises(sa.exc.DBAPIError, match="governed document version"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM document_versions WHERE id=:id"),
                {"id": evidence["version_id"]},
            )


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM document_evidence_spans WHERE id=:span",
        "DELETE FROM document_chunk_blocks WHERE id=:link",
        "DELETE FROM document_blocks WHERE id=:block",
        "DELETE FROM document_chunks WHERE id=:chunk",
        "INSERT INTO document_blocks (id, org_id, document_version_id, ordinal, text, "
        "page_number, locator_kind, locator_label, block_type, section_path, "
        "extraction_method, ocr_profile_version, content_hash, created_at) VALUES "
        "(:new, :org, :version, 1, 'late', 1, 'page', '1', 'paragraph', "
        "'[\"Safety\"]'::jsonb, 'native', 'none/v1', :hash, now())",
        "INSERT INTO document_chunks (id, org_id, document_version_id, ordinal, text, "
        "token_count, page_start, page_end, section_path, content_hash, "
        "chunking_profile_version, embedding_profile_version, created_at) VALUES "
        "(:new, :org, :version, 1, 'late', 1, 1, 1, '[\"Safety\"]'::jsonb, "
        ":hash, 'chunk/v2', 'embed/v2', now())",
        "INSERT INTO document_chunk_blocks (id, org_id, document_version_id, chunk_id, "
        "block_id, position, created_at) VALUES "
        "(:new, :org, :version, :chunk, :block, 1, now())",
        "INSERT INTO document_evidence_spans (id, org_id, document_version_id, chunk_id, "
        "page_number, locator_kind, locator_label, section_path, content_hash, ordinal, "
        "token_count, artifact_byte_start, artifact_byte_end, created_at) VALUES "
        "(:new, :org, :version, :chunk, 1, 'page', '1', '[\"Safety\"]'::jsonb, "
        ":hash, 1, 1, 0, 4, now())",
    ],
)
def test_ready_provenance_rejects_insert_and_delete_for_every_artifact(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    mutation: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)

    with pytest.raises(sa.exc.DBAPIError, match="evidence artifact"):
        with engine.begin() as connection:
            connection.execute(
                text(mutation),
                {
                    **evidence,
                    "new": uuid4(),
                    "org": ids.org_id,
                    "version": evidence["version_id"],
                    "hash": "9" * 64,
                    "span": evidence["span_id"],
                    "link": evidence["link_id"],
                    "block": evidence["block_id"],
                    "chunk": evidence["chunk_id"],
                },
            )


def test_governed_ready_version_rejects_parent_cascade_delete(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)

    with pytest.raises(sa.exc.DBAPIError, match="governed document version"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM document_versions WHERE id=:id"),
                {"id": evidence["version_id"]},
            )


@pytest.mark.parametrize("artifact_kind", ["block", "chunk"])
def test_downgrade_rejects_block_or_chunk_only_derived_artifact(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    artifact_kind: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    version_id = ids.document_ids["indexed"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET state='processing', provenance_state='none' "
                "WHERE id=:id"
            ),
            {"id": version_id},
        )
        if artifact_kind == "chunk":
            connection.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(id, org_id, document_version_id, ordinal, text, token_count, "
                    "page_start, page_end, section_path, content_hash, "
                    "chunking_profile_version, embedding_profile_version, created_at) "
                    "VALUES (:id, :org, :version, 0, 'legacy derived chunk', 3, 1, 1, "
                    "'[\"Legacy import\"]'::jsonb, :hash, 'legacy/chunking-v1', "
                    "'legacy/embedding-v1', now())"
                ),
                {
                    "id": uuid4(),
                    "org": ids.org_id,
                    "version": version_id,
                    "hash": "5" * 64,
                },
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO document_blocks "
                    "(id, org_id, document_version_id, parent_block_id, ordinal, text, "
                    "page_number, locator_kind, locator_label, block_type, section_path, "
                    "source_coordinates, extraction_method, ocr_profile_version, "
                    "ocr_confidence, content_hash, created_at) VALUES "
                    "(:id, :org, :version, NULL, 0, 'legacy derived block', 1, 'page', "
                    "'1', 'paragraph', '[\"Legacy import\"]'::jsonb, NULL, 'legacy', "
                    "'legacy/ocr-unknown-v1', NULL, :hash, now())"
                ),
                {
                    "id": uuid4(),
                    "org": ids.org_id,
                    "version": version_id,
                    "hash": "6" * 64,
                },
            )

    with pytest.raises(RuntimeError, match="derived artifact"):
        command.downgrade(config, "6c4a2f8b9d10")


@pytest.mark.parametrize(
    "scope_override",
    [
        {"org_id": uuid4()},
        {"workspace_id": uuid4()},
        {"document_id": uuid4()},
    ],
)
def test_authority_citation_rejects_cross_scope_parent_and_members(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    scope_override: dict[str, object],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids)
            connection.execute(
                text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
                {"id": assistant},
            )
            _insert_authority_citation(
                connection,
                ids,
                evidence,
                assistant,
                **scope_override,
            )


def test_downgrade_rejects_multiple_document_versions(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        evidence = _seed_authority_evidence(connection, ids)
        assert evidence["version_id"] is not None

    with pytest.raises(RuntimeError, match="multiple versions"):
        command.downgrade(config, "6c4a2f8b9d10")


def test_downgrade_rejects_orphaned_document_version_inbox_event(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, _ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    event_id = uuid4()
    inbox_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO outbox_events "
                "(event_id, aggregate_type, aggregate_id, event_type, payload, "
                "dedupe_key, attempts, id, created_at) VALUES "
                "(:event, 'document_version', :aggregate, "
                "'document.version.rebuild_requested.v1', '{}'::json, :dedupe, 0, "
                ":id, now())"
            ),
            {
                "event": event_id,
                "aggregate": uuid4(),
                "dedupe": str(uuid4()),
                "id": uuid4(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO inbox_events (consumer, event_id, event_type, id, created_at) "
                "VALUES ('document-start-v1', :event, "
                "'document.version.rebuild_requested.v1', :id, now())"
            ),
            {"event": event_id, "id": inbox_id},
        )
        connection.execute(
            text("DELETE FROM outbox_events WHERE event_id=:event"),
            {"event": event_id},
        )

    with pytest.raises(RuntimeError, match="document version event"):
        command.downgrade(config, "6c4a2f8b9d10")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT event_type FROM inbox_events WHERE id=:id"),
            {"id": inbox_id},
        ).scalar_one() == "document.version.rebuild_requested.v1"


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"page": 2},
        {"document_version_id": "legacy"},
        {"evidence_span_id": uuid4()},
    ],
)
def test_authority_citation_rejects_span_or_version_mismatch(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    overrides: dict[str, object],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids)
            connection.execute(
                text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
                {"id": assistant},
            )
            if overrides.get("document_version_id") == "legacy":
                overrides = {
                    **overrides,
                    "document_version_id": ids.document_ids["indexed"],
                }
            _insert_authority_citation(
                connection,
                ids,
                evidence,
                assistant,
                **overrides,
            )


@pytest.mark.parametrize(
    "eligibility_mutation",
    [
        "UPDATE grounding_policies SET status='retired' WHERE id=:policy",
        "UPDATE models SET enabled=false WHERE id=:model",
        "UPDATE models SET sync_status='error' WHERE id=:model",
        "UPDATE models SET supports_verifier=false WHERE id=:model",
        "UPDATE document_versions SET state='processing' WHERE id=:version",
    ],
)
def test_authority_citation_rechecks_current_policy_model_and_version_eligibility(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    eligibility_mutation: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(connection, ids)
            connection.execute(
                text(eligibility_mutation),
                {
                    "policy": evidence["policy_id"],
                    "model": evidence["model_id"],
                    "version": evidence["version_id"],
                },
            )
            connection.execute(
                text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
                {"id": assistant},
            )
            _insert_authority_citation(connection, ids, evidence, assistant)


@pytest.mark.parametrize(
    ("effective_at", "expires_at"),
    [
        (datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1), None),
        (None, datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)),
    ],
)
def test_authority_citation_rejects_version_outside_effective_window(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    effective_at: datetime | None,
    expires_at: datetime | None,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    _chat, _user, assistant = _seed_legacy_parent(engine, ids)
    with pytest.raises(sa.exc.DBAPIError, match="authority citation"):
        with engine.begin() as connection:
            evidence = _seed_authority_evidence(
                connection,
                ids,
                effective_at=effective_at,
                expires_at=expires_at,
            )
            connection.execute(
                text("UPDATE messages SET answer_status='grounded' WHERE id=:id"),
                {"id": assistant},
            )
            _insert_authority_citation(connection, ids, evidence, assistant)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE documents SET owner_id=:actor WHERE id=:document",
        "UPDATE documents SET created_by=:actor WHERE id=:document",
        "UPDATE document_versions SET created_by=:actor WHERE id=:document",
        "UPDATE document_versions SET approved_by=:actor WHERE id=:document",
        "UPDATE document_versions SET rejected_by=:actor WHERE id=:document",
        "UPDATE document_versions SET obsolete_by=:actor WHERE id=:document",
    ],
)
def test_cross_organization_document_actor_is_database_rejected(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    mutation: str,
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    other_org = uuid4()
    other_user = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations (id, name, created_at) VALUES (:id, :name, now())"),
            {"id": other_org, "name": f"Other {other_org}"},
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, org_id, email, password_hash, active, is_platform_superadmin, "
                "created_at) VALUES (:id, :org, :email, 'inert', true, false, now())"
            ),
            {
                "id": other_user,
                "org": other_org,
                "email": f"{other_user}@example.com",
            },
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(mutation),
                {
                    "actor": other_user,
                    "document": ids.document_ids["indexed"],
                },
            )
