import threading
from concurrent.futures import ThreadPoolExecutor
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
) -> dict[str, object]:
    document_id = ids.document_ids["failed"]
    version_id = uuid4()
    model_id = uuid4()
    policy_id = uuid4()
    chunk_id = uuid4()
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
            "lifecycle_revision, created_by, updated_at, created_at) VALUES "
            "(:id, :org, :workspace, :document, 2, 'Approved 2', 'approved 2', :hash, "
            "'approved-2.pdf', 'application/pdf', 10, 'approved-2.pdf', 1, "
            "'parser/v2', 'none/v1', 'chunk/v2', 'embed/v2', 'index/v2', "
            "'approved', 'ready', 1, :user, now(), now())"
        ),
        {
            "id": version_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "document": document_id,
            "hash": "c" * 64,
            "user": ids.user_id,
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
    return {
        "document_id": document_id,
        "version_id": version_id,
        "model_id": model_id,
        "policy_id": policy_id,
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
            ":page, 0.95, 1, 'failed.pdf', 'Approved 2', 'Safety', "
            "'[\"Safety\"]'::jsonb, 'page', '1', :hash, '[\"claim-1\"]'::jsonb, "
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
            "hash": evidence["content_hash"],
            "policy": evidence["policy_id"],
            "model": evidence["model_id"],
        },
    )
    return citation_id


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


def test_readiness_generation_preserves_signed_tenant_snapshot_and_is_terminal(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    readiness_id = uuid4()
    generation_id = uuid4()
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
                "binding_revision, credential_fingerprint, readiness_digest, signature, "
                "blocker_codes, status, attempts, expires_at, created_at) VALUES "
                "(:id, :generation, :org, :workspace, :request_digest, 'authority-v1', "
                "'authority-current', 1, 1, 1, 1, 1, :policy, 1, :calibration_hash, "
                ":model, 'preset/v1', 'binding/v1', 'credential-v1', "
                ":readiness_digest, :signature, ARRAY[]::varchar[], 'failed', 1, "
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
                "readiness_digest": "e" * 64,
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
