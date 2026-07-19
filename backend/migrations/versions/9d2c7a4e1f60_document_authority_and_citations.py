"""document authority and citations

Revision ID: 9d2c7a4e1f60
Revises: 6c4a2f8b9d10
Create Date: 2026-07-19 08:32:49.338064

"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9d2c7a4e1f60"
down_revision: str | Sequence[str] | None = "6c4a2f8b9d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _lock_legacy_tables(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "LOCK TABLE documents, ingest_jobs, chats, messages, citations, "
            "outbox_events, inbox_events IN SHARE ROW EXCLUSIVE MODE"
        )
    )


def _reject_invalid_legacy_citations(connection: sa.Connection) -> None:
    invalid = connection.execute(
        sa.text(
            "SELECT count(*) FROM citations c "
            "LEFT JOIN messages m ON m.id=c.message_id "
            "LEFT JOIN chats ch ON ch.id=m.chat_id "
            "LEFT JOIN documents d ON d.id=c.document_id "
            "WHERE m.id IS NULL OR ch.id IS NULL OR d.id IS NULL OR c.page <= 0 "
            "OR m.role IS DISTINCT FROM 'assistant' "
            "OR d.org_id <> ch.org_id OR d.workspace_id <> ch.workspace_id"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError(
            "document authority migration aborted: orphan or invalid legacy citation"
        )


def _prepare_compatibility_tables() -> None:
    """Expand tenant scope before new tables reference composite keys."""

    op.add_column(
        "inbox_events", sa.Column("event_type", sa.String(length=200), nullable=True)
    )
    op.execute(
        "UPDATE inbox_events i SET event_type=o.event_type "
        "FROM outbox_events o WHERE o.event_id=i.event_id"
    )
    # Old Inbox rows whose Outbox envelope was already pruned cannot be assigned
    # an invented event identity. Keep a bounded explicit unknown sentinel; all
    # new consumers must persist the exact event type.
    op.execute("UPDATE inbox_events SET event_type='legacy.unknown' WHERE event_type IS NULL")
    op.alter_column("inbox_events", "event_type", nullable=False)
    op.create_check_constraint(
        "ck_inbox_events_event_type_bounded",
        "inbox_events",
        "char_length(event_type) BETWEEN 1 AND 200",
    )
    op.create_index(
        op.f("ix_inbox_events_event_type"),
        "inbox_events",
        ["event_type"],
        unique=False,
    )

    op.add_column(
        "workspaces", sa.Column("document_authority_enabled", sa.Boolean(), nullable=True)
    )
    op.execute("UPDATE workspaces SET document_authority_enabled=false")
    op.alter_column("workspaces", "document_authority_enabled", nullable=False)

    op.add_column("documents", sa.Column("name", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("department", sa.String(length=120), nullable=True))
    op.add_column("documents", sa.Column("document_type", sa.String(length=120), nullable=True))
    op.add_column(
        "documents", sa.Column("external_identifier", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("acl_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("documents", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE documents SET name=filename")
    op.alter_column("documents", "name", nullable=False)
    for column, column_type in (
        ("filename", sa.VARCHAR()),
        ("mime", sa.VARCHAR()),
        ("size_bytes", sa.INTEGER()),
        ("content_hash", sa.VARCHAR()),
        ("status", sa.VARCHAR()),
        ("storage_key", sa.VARCHAR()),
    ):
        op.alter_column("documents", column, existing_type=column_type, nullable=True)
    op.drop_constraint("uq_documents_workspace_hash", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_documents_org_workspace_external_identifier",
        "documents",
        ["org_id", "workspace_id", "external_identifier"],
    )
    op.create_unique_constraint(
        "uq_documents_org_workspace_id", "documents", ["org_id", "workspace_id", "id"]
    )
    op.create_check_constraint(
        "ck_documents_acl_policy",
        "documents",
        "acl_policy IS NULL OR "
        "(jsonb_typeof(acl_policy)='object' AND pg_column_size(acl_policy) <= 8192)",
    )
    op.drop_constraint("documents_created_by_fkey", "documents", type_="foreignkey")
    op.drop_constraint("documents_workspace_id_fkey", "documents", type_="foreignkey")
    op.create_foreign_key(
        "fk_documents_org_workspace",
        "documents",
        "workspaces",
        ["org_id", "workspace_id"],
        ["org_id", "id"],
    )
    op.create_foreign_key(
        "fk_documents_org_creator",
        "documents",
        "users",
        ["org_id", "created_by"],
        ["org_id", "id"],
    )
    op.create_foreign_key(
        "fk_documents_org_owner",
        "documents",
        "users",
        ["org_id", "owner_id"],
        ["org_id", "id"],
    )

    op.create_unique_constraint(
        "uq_chats_org_workspace_id", "chats", ["org_id", "workspace_id", "id"]
    )
    op.drop_constraint("chats_user_id_fkey", "chats", type_="foreignkey")
    op.drop_constraint("chats_workspace_id_fkey", "chats", type_="foreignkey")
    op.create_foreign_key(
        "fk_chats_org_user", "chats", "users", ["org_id", "user_id"], ["org_id", "id"]
    )
    op.create_foreign_key(
        "fk_chats_org_workspace",
        "chats",
        "workspaces",
        ["org_id", "workspace_id"],
        ["org_id", "id"],
    )

    op.add_column("messages", sa.Column("org_id", sa.Uuid(), nullable=True))
    op.add_column("messages", sa.Column("workspace_id", sa.Uuid(), nullable=True))
    op.add_column("messages", sa.Column("answer_status", sa.String(length=32), nullable=True))
    op.add_column("messages", sa.Column("refusal_reason", sa.String(length=64), nullable=True))
    op.add_column("messages", sa.Column("grounding_policy_id", sa.Uuid(), nullable=True))
    op.add_column("messages", sa.Column("grounding_policy_version", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("verifier_model_id", sa.Uuid(), nullable=True))
    op.add_column(
        "messages", sa.Column("prompt_contract_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "messages", sa.Column("provider_preset_version", sa.String(length=100), nullable=True)
    )
    op.add_column("messages", sa.Column("binding_revision", sa.String(length=100), nullable=True))
    op.add_column(
        "messages", sa.Column("credential_fingerprint", sa.String(length=128), nullable=True)
    )
    op.execute(
        "UPDATE messages m SET org_id=c.org_id, workspace_id=c.workspace_id "
        "FROM chats c WHERE c.id=m.chat_id"
    )
    op.alter_column("messages", "org_id", nullable=False)
    op.alter_column("messages", "workspace_id", nullable=False)
    op.create_index("ix_messages_org_id", "messages", ["org_id"])
    op.create_index("ix_messages_workspace_id", "messages", ["workspace_id"])
    op.create_unique_constraint("uq_messages_chat_id", "messages", ["chat_id", "id"])
    op.create_unique_constraint(
        "uq_messages_org_workspace_id", "messages", ["org_id", "workspace_id", "id"]
    )
    op.create_check_constraint(
        "ck_messages_answer_status",
        "messages",
        "answer_status IS NULL OR answer_status IN ('grounded','cited_conflict','refused')",
    )
    op.create_check_constraint(
        "ck_messages_refusal_reason",
        "messages",
        "answer_status IS NULL OR "
        "(answer_status='refused' AND refusal_reason IS NOT NULL) OR "
        "(answer_status<>'refused' AND refusal_reason IS NULL)",
    )
    op.create_check_constraint("ck_messages_role", "messages", "role IN ('user','assistant')")
    op.drop_constraint("messages_parent_message_id_fkey", "messages", type_="foreignkey")
    op.create_foreign_key(
        "fk_messages_org_workspace_chat",
        "messages",
        "chats",
        ["org_id", "workspace_id", "chat_id"],
        ["org_id", "workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_messages_same_chat_parent",
        "messages",
        "messages",
        ["chat_id", "parent_message_id"],
        ["chat_id", "id"],
        ondelete="CASCADE",
    )

    for column in (
        "supports_chat_completion",
        "supports_structured_json",
        "supports_verifier",
    ):
        op.add_column(
            "models",
            sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.alter_column("models", column, server_default=None)
    op.add_column(
        "models", sa.Column("provider_preset_version", sa.String(length=100), nullable=True)
    )
    op.add_column("outbox_events", sa.Column("lease_owner", sa.String(), nullable=True))
    op.add_column("outbox_events", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.create_index("ix_outbox_events_lease_owner", "outbox_events", ["lease_owner"])
    op.create_index("ix_outbox_events_lease_expires_at", "outbox_events", ["lease_expires_at"])


def _backfill_versions_and_jobs(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO document_versions "
            "(id, org_id, workspace_id, document_id, sequence, version_label, version_key, "
            "content_hash, source_filename, source_mime, source_size_bytes, "
            "source_storage_key, source_page_count, parser_profile_version, "
            "ocr_profile_version, chunking_profile_version, embedding_profile_version, "
            "index_profile_version, state, provenance_state, lifecycle_revision, created_by, "
            "updated_at, created_at) "
            "SELECT id, org_id, workspace_id, id, 1, 'Legacy 1', 'legacy 1', content_hash, "
            "filename, mime, size_bytes, storage_key, "
            "CASE WHEN page_count > 0 THEN page_count ELSE NULL END, "
            "'legacy/parser-v1', 'legacy/ocr-unknown-v1', 'legacy/chunking-v1', "
            "'legacy/embedding-v1', 'legacy/index-v1', "
            "CASE WHEN status='indexed' THEN 'approved' WHEN status='failed' THEN 'failed' "
            "ELSE 'processing' END, "
            "CASE WHEN status='indexed' THEN 'legacy_pending' ELSE 'none' END, "
            "1, created_by, updated_at, created_at FROM documents"
        )
    )
    op.add_column("ingest_jobs", sa.Column("org_id", sa.Uuid(), nullable=True))
    op.add_column("ingest_jobs", sa.Column("document_version_id", sa.Uuid(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE ingest_jobs j SET org_id=d.org_id, document_version_id=d.id "
            "FROM documents d WHERE d.id=j.document_id"
        )
    )
    op.alter_column("ingest_jobs", "document_id", existing_type=sa.UUID(), nullable=True)
    op.create_index("ix_ingest_jobs_org_id", "ingest_jobs", ["org_id"])
    op.create_index("ix_ingest_jobs_document_version_id", "ingest_jobs", ["document_version_id"])
    op.create_foreign_key(
        "fk_ingest_jobs_org_document_version",
        "ingest_jobs",
        "document_versions",
        ["org_id", "document_id", "document_version_id"],
        ["org_id", "document_id", "id"],
    )


def _expand_and_backfill_citations(connection: sa.Connection) -> None:
    citation_columns = (
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("document_version_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_span_id", sa.Uuid(), nullable=True),
        sa.Column("document_name", sa.String(length=500), nullable=True),
        sa.Column("version_label", sa.String(length=200), nullable=True),
        sa.Column("section_label", sa.String(length=500), nullable=True),
        sa.Column("section_path", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("locator_kind", sa.String(length=32), nullable=True),
        sa.Column("locator_label", sa.String(length=200), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("dense_score", sa.Float(), nullable=True),
        sa.Column("sparse_score", sa.Float(), nullable=True),
        sa.Column("fused_score", sa.Float(), nullable=True),
        sa.Column("rerank_score", sa.Float(), nullable=True),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("claim_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("verification_state", sa.String(length=32), nullable=True),
        sa.Column("prompt_contract_version", sa.String(length=100), nullable=True),
        sa.Column("grounding_policy_id", sa.Uuid(), nullable=True),
        sa.Column("grounding_policy_version", sa.Integer(), nullable=True),
        sa.Column("verifier_model_id", sa.Uuid(), nullable=True),
        sa.Column("provider_preset_version", sa.String(length=100), nullable=True),
        sa.Column("binding_revision", sa.String(length=100), nullable=True),
        sa.Column("credential_fingerprint", sa.String(length=128), nullable=True),
    )
    for column in citation_columns:
        op.add_column("citations", column)
    connection.execute(
        sa.text(
            "UPDATE citations c SET org_id=m.org_id, workspace_id=m.workspace_id, "
            "document_version_id=d.id, document_name=d.name, version_label='Legacy 1', "
            "section_label='Legacy import', section_path='[\"Legacy import\"]'::jsonb, "
            "locator_kind='page', locator_label=c.page::text, "
            "content_hash='legacy-unverified', claim_ids='[]'::jsonb, "
            "verification_state='legacy_unverified' "
            "FROM messages m, documents d "
            "WHERE m.id=c.message_id AND d.id=c.document_id"
        )
    )
    for column in ("org_id", "workspace_id", "document_version_id"):
        op.alter_column("citations", column, nullable=False)


def _finalize_citations() -> None:
    for column in ("org_id", "workspace_id", "document_version_id", "evidence_span_id"):
        op.create_index(f"ix_citations_{column}", "citations", [column])
    op.create_check_constraint("ck_citations_page_positive", "citations", "page > 0")
    op.create_check_constraint(
        "ck_citations_section_path",
        "citations",
        "section_path IS NULL OR (jsonb_typeof(section_path)='array' "
        "AND jsonb_array_length(section_path) BETWEEN 1 AND 8 "
        "AND pg_column_size(section_path) <= 4096 "
        "AND jsonb_array_length(jsonb_path_query_array(section_path, "
        '\'$[*] ? (@.type() == "string" && @ like_regex "^.{1,200}$" flag "s")\')) '
        "= jsonb_array_length(section_path))",
    )
    op.create_check_constraint(
        "ck_citations_claim_ids",
        "citations",
        "claim_ids IS NULL OR (jsonb_typeof(claim_ids)='array' "
        "AND jsonb_array_length(claim_ids) BETWEEN 0 AND 64 "
        "AND pg_column_size(claim_ids) <= 8192 "
        "AND jsonb_array_length(jsonb_path_query_array(claim_ids, "
        '\'$[*] ? (@.type() == "string" && @ like_regex "^.{1,64}$" flag "s")\')) '
        "= jsonb_array_length(claim_ids))",
    )
    op.create_check_constraint(
        "ck_citations_content_hash_sha256",
        "citations",
        "content_hash IS NULL OR content_hash='legacy-unverified' "
        "OR content_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_citations_snapshot_strings_bounded",
        "citations",
        "(document_name IS NULL OR char_length(document_name) BETWEEN 1 AND 500) "
        "AND (version_label IS NULL OR char_length(version_label) BETWEEN 1 AND 200) "
        "AND (section_label IS NULL OR char_length(section_label) BETWEEN 1 AND 500) "
        "AND (locator_kind IS NULL OR char_length(locator_kind) BETWEEN 1 AND 32) "
        "AND (locator_label IS NULL OR char_length(locator_label) BETWEEN 1 AND 200)",
    )
    op.create_check_constraint(
        "ck_citations_legacy_or_authority_snapshot",
        "citations",
        "(verification_state='legacy_unverified' "
        "AND document_version_id IS NOT NULL AND evidence_span_id IS NULL "
        "AND document_name IS NOT NULL AND version_label='Legacy 1' "
        "AND section_label='Legacy import' "
        "AND section_path='[\"Legacy import\"]'::jsonb "
        "AND locator_kind='page' AND locator_label=CAST(page AS text) "
        "AND content_hash='legacy-unverified' AND claim_ids='[]'::jsonb "
        "AND claim_id IS NULL AND dense_score IS NULL AND sparse_score IS NULL "
        "AND fused_score IS NULL AND rerank_score IS NULL "
        "AND prompt_contract_version IS NULL AND grounding_policy_id IS NULL "
        "AND grounding_policy_version IS NULL AND verifier_model_id IS NULL "
        "AND provider_preset_version IS NULL AND binding_revision IS NULL "
        "AND credential_fingerprint IS NULL) OR "
        "(verification_state IS NOT NULL AND verification_state<>'legacy_unverified' "
        "AND document_version_id IS NOT NULL AND evidence_span_id IS NOT NULL "
        "AND document_name IS NOT NULL AND version_label IS NOT NULL "
        "AND section_label IS NOT NULL AND section_path IS NOT NULL "
        "AND locator_kind IS NOT NULL AND locator_label IS NOT NULL "
        "AND content_hash ~ '^[0-9a-f]{64}$' "
        "AND jsonb_array_length(claim_ids) BETWEEN 1 AND 64 "
        "AND prompt_contract_version IS NOT NULL AND grounding_policy_id IS NOT NULL "
        "AND grounding_policy_version > 0 AND verifier_model_id IS NOT NULL "
        "AND provider_preset_version IS NOT NULL AND binding_revision IS NOT NULL "
        "AND credential_fingerprint IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_citations_org_workspace_message",
        "citations",
        "messages",
        ["org_id", "workspace_id", "message_id"],
        ["org_id", "workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_citations_org_workspace_document",
        "citations",
        "documents",
        ["org_id", "workspace_id", "document_id"],
        ["org_id", "workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_citations_org_document_version",
        "citations",
        "document_versions",
        ["org_id", "document_id", "document_version_id", "version_label"],
        ["org_id", "document_id", "id", "version_label"],
    )
    op.create_foreign_key(
        "fk_citations_org_version_evidence_span",
        "citations",
        "document_evidence_spans",
        ["org_id", "document_version_id", "evidence_span_id"],
        ["org_id", "document_version_id", "id"],
    )
    op.create_index(
        "uq_document_versions_one_approved",
        "document_versions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("state='approved' AND superseded_by_id IS NULL"),
    )


def _install_authority_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION openrag_validate_document_version_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(OLD.org_id, OLD.workspace_id, OLD.document_id, OLD.sequence,
                 OLD.version_label, OLD.version_key, OLD.source_filename,
                 OLD.source_mime, OLD.source_size_bytes, OLD.content_hash,
                 OLD.source_storage_key, OLD.revision_date, OLD.effective_at,
                 OLD.expires_at)
             IS DISTINCT FROM
             ROW(NEW.org_id, NEW.workspace_id, NEW.document_id, NEW.sequence,
                 NEW.version_label, NEW.version_key, NEW.source_filename,
                 NEW.source_mime, NEW.source_size_bytes, NEW.content_hash,
                 NEW.source_storage_key, NEW.revision_date, NEW.effective_at,
                 NEW.expires_at) THEN
            RAISE EXCEPTION 'document version identity and source snapshot are immutable';
          END IF;
          IF OLD.provenance_state='ready'
             AND OLD.provenance_state IS DISTINCT FROM NEW.provenance_state THEN
            RAISE EXCEPTION 'ready document provenance is immutable';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_versions_immutable
        BEFORE UPDATE ON document_versions FOR EACH ROW
        EXECUTE FUNCTION openrag_validate_document_version_update();
        """
    )
    op.execute(
        """
        CREATE FUNCTION openrag_validate_document_version_delete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.state NOT IN ('draft','processing','rejected','failed') THEN
            RAISE EXCEPTION 'governed document version history cannot be deleted';
          END IF;
          RETURN OLD;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_versions_delete_lifecycle
        BEFORE DELETE ON document_versions FOR EACH ROW
        EXECUTE FUNCTION openrag_validate_document_version_delete();
        """
    )
    op.execute(
        """
        CREATE FUNCTION openrag_validate_citation_write() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          authority_enabled boolean;
          parent_role text;
          parent_status text;
          parent_refusal text;
          version_sequence integer;
          stored_version_label text;
          stored_version_key text;
          stored_document_name text;
          stored_version_state text;
          stored_provenance_state text;
          stored_superseded_by_id uuid;
          stored_effective_at timestamp;
          stored_expires_at timestamp;
          span_hash text;
          span_page integer;
          span_locator_kind text;
          span_locator_label text;
          span_section_path jsonb;
          span_section_label text;
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'citation snapshots are immutable';
          END IF;

          SELECT w.document_authority_enabled INTO authority_enabled
          FROM workspaces w
          WHERE w.org_id=NEW.org_id AND w.id=NEW.workspace_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'citation workspace scope is invalid';
          END IF;

          SELECT m.role, m.answer_status, m.refusal_reason
          INTO parent_role, parent_status, parent_refusal
          FROM messages m
          WHERE m.org_id=NEW.org_id AND m.workspace_id=NEW.workspace_id
            AND m.id=NEW.message_id
          FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'citation parent scope is invalid';
          END IF;

          SELECT v.sequence, v.version_label, v.version_key, d.name,
                 v.state, v.provenance_state, v.superseded_by_id,
                 v.effective_at, v.expires_at
          INTO version_sequence, stored_version_label, stored_version_key,
               stored_document_name, stored_version_state,
               stored_provenance_state, stored_superseded_by_id,
               stored_effective_at, stored_expires_at
          FROM document_versions v
          JOIN documents d ON (d.org_id, d.workspace_id, d.id)=
                              (v.org_id, v.workspace_id, v.document_id)
          WHERE v.org_id=NEW.org_id AND v.workspace_id=NEW.workspace_id
            AND v.document_id=NEW.document_id AND v.id=NEW.document_version_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'citation document version scope is invalid';
          END IF;

          IF NEW.verification_state='legacy_unverified' THEN
            IF authority_enabled
               OR parent_role<>'assistant' OR parent_status IS NOT NULL
               OR parent_refusal IS NOT NULL OR version_sequence<>1
               OR stored_version_label<>'Legacy 1' OR stored_version_key<>'legacy 1'
               OR NEW.version_label<>'Legacy 1'
               OR NEW.document_name IS DISTINCT FROM stored_document_name
               OR NEW.document_name IS NULL OR btrim(NEW.document_name)=''
               OR NEW.section_label<>'Legacy import'
               OR NEW.section_path<>'[\"Legacy import\"]'::jsonb
               OR NEW.page<=0 OR NEW.locator_kind<>'page'
               OR NEW.locator_label IS DISTINCT FROM NEW.page::text
               OR NEW.content_hash<>'legacy-unverified'
               OR NEW.claim_ids IS DISTINCT FROM '[]'::jsonb
               OR NEW.claim_id IS NOT NULL OR NEW.evidence_span_id IS NOT NULL
               OR NEW.dense_score IS NOT NULL OR NEW.sparse_score IS NOT NULL
               OR NEW.fused_score IS NOT NULL OR NEW.rerank_score IS NOT NULL
               OR NEW.prompt_contract_version IS NOT NULL
               OR NEW.grounding_policy_id IS NOT NULL
               OR NEW.grounding_policy_version IS NOT NULL
               OR NEW.verifier_model_id IS NOT NULL
               OR NEW.provider_preset_version IS NOT NULL
               OR NEW.binding_revision IS NOT NULL
               OR NEW.credential_fingerprint IS NOT NULL THEN
              RAISE EXCEPTION 'invalid legacy_unverified citation snapshot';
            END IF;
          ELSE
            IF parent_role<>'assistant'
               OR parent_status NOT IN ('grounded','cited_conflict')
               OR parent_refusal IS NOT NULL
               OR NEW.verification_state IS NULL
               OR NEW.verification_state='legacy_unverified'
               OR stored_version_state<>'approved'
               OR stored_provenance_state<>'ready'
               OR stored_superseded_by_id IS NOT NULL
               OR (stored_effective_at IS NOT NULL AND stored_effective_at>now())
               OR (stored_expires_at IS NOT NULL AND stored_expires_at<=now())
               OR NEW.document_name IS DISTINCT FROM stored_document_name
               OR NEW.version_label IS DISTINCT FROM stored_version_label
               OR NEW.document_name IS NULL OR btrim(NEW.document_name)=''
               OR NEW.section_label IS NULL OR btrim(NEW.section_label)=''
               OR NEW.locator_kind IS NULL OR btrim(NEW.locator_kind)=''
               OR NEW.locator_label IS NULL OR btrim(NEW.locator_label)=''
               OR NEW.evidence_span_id IS NULL
               OR NEW.content_hash !~ '^[0-9a-f]{64}$'
               OR jsonb_typeof(NEW.claim_ids)<>'array'
               OR jsonb_array_length(NEW.claim_ids)<1 THEN
              RAISE EXCEPTION 'invalid authority citation snapshot';
            END IF;

            SELECT e.content_hash, e.page_number, e.locator_kind, e.locator_label,
                   e.section_path,
                   e.section_path ->> (jsonb_array_length(e.section_path) - 1)
            INTO span_hash, span_page, span_locator_kind, span_locator_label,
                 span_section_path, span_section_label
            FROM document_evidence_spans e
            WHERE e.org_id=NEW.org_id
              AND e.document_version_id=NEW.document_version_id
              AND e.id=NEW.evidence_span_id;
            IF NOT FOUND OR NEW.content_hash IS DISTINCT FROM span_hash
               OR NEW.page IS DISTINCT FROM span_page
               OR NEW.locator_kind IS DISTINCT FROM span_locator_kind
               OR NEW.locator_label IS DISTINCT FROM span_locator_label
               OR NEW.section_path IS DISTINCT FROM span_section_path
               OR NEW.section_label IS DISTINCT FROM span_section_label THEN
              RAISE EXCEPTION 'authority citation does not reproduce immutable evidence';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM grounding_policies p
              JOIN models verifier ON verifier.id=p.verifier_model_id
              WHERE p.org_id=NEW.org_id AND p.workspace_id=NEW.workspace_id
                AND p.id=NEW.grounding_policy_id
                AND p.policy_version=NEW.grounding_policy_version
                AND p.verifier_model_id=NEW.verifier_model_id
                AND p.provider_preset_version=NEW.provider_preset_version
                AND p.binding_revision=NEW.binding_revision
                AND p.credential_fingerprint=NEW.credential_fingerprint
                AND p.status='active'
                AND (p.effective_at IS NULL OR p.effective_at<=now())
                AND (p.expires_at IS NULL OR p.expires_at>now())
                AND verifier.enabled
                AND verifier.sync_status='ready'
                AND verifier.supports_verifier
            ) THEN
              RAISE EXCEPTION 'authority citation grounding binding is invalid';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_citations_validate_write
        BEFORE INSERT OR UPDATE ON citations FOR EACH ROW
        EXECUTE FUNCTION openrag_validate_citation_write();
        """
    )
    op.execute(
        """
        CREATE FUNCTION openrag_validate_message_final_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          checked_message_id uuid;
          current_role text;
          current_status text;
          current_refusal text;
          citation_count integer;
          authority_count integer;
        BEGIN
          IF TG_TABLE_NAME='citations' THEN
            checked_message_id := CASE WHEN TG_OP='DELETE' THEN OLD.message_id
                                       ELSE NEW.message_id END;
          ELSE
            checked_message_id := CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END;
          END IF;
          SELECT role, answer_status, refusal_reason
          INTO current_role, current_status, current_refusal
          FROM messages WHERE id=checked_message_id;
          IF NOT FOUND THEN
            RETURN NULL;
          END IF;
          SELECT count(*), count(*) FILTER (
                   WHERE verification_state<>'legacy_unverified'
                 )
          INTO citation_count, authority_count
          FROM citations WHERE message_id=checked_message_id;

          IF current_role='user' THEN
            IF current_status IS NOT NULL OR current_refusal IS NOT NULL
               OR citation_count<>0 THEN
              RAISE EXCEPTION 'user messages cannot carry answer state or citations';
            END IF;
          ELSIF current_status IS NULL THEN
            IF current_refusal IS NOT NULL OR authority_count<>0 THEN
              RAISE EXCEPTION 'historical assistants accept only legacy display citations';
            END IF;
          ELSIF current_status='refused' THEN
            IF current_refusal NOT IN (
              'no_eligible_documents','no_candidates','below_threshold',
              'incomplete_provenance','conflicting_evidence','index_projection_lag',
              'entailment_failed','citation_validation_failed'
            ) OR citation_count<>0 THEN
              RAISE EXCEPTION 'refused messages require a controlled reason and zero citations';
            END IF;
          ELSIF current_status IN ('grounded','cited_conflict') THEN
            IF current_refusal IS NOT NULL OR authority_count<1 THEN
              RAISE EXCEPTION 'grounded messages require authority evidence';
            END IF;
          ELSE
            RAISE EXCEPTION 'invalid assistant answer state';
          END IF;
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_messages_final_state
        AFTER INSERT OR UPDATE ON messages DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION openrag_validate_message_final_state();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_citations_parent_final_state
        AFTER INSERT OR UPDATE OR DELETE ON citations DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION openrag_validate_message_final_state();
        """
    )
    op.execute(
        """
        CREATE FUNCTION openrag_protect_terminal_readiness() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status IN ('stale','failed','activated') THEN
            RAISE EXCEPTION 'terminal readiness generation is immutable';
          END IF;
          IF OLD.status='building' THEN
            IF NEW.status NOT IN ('building','passed','stale','failed') THEN
              RAISE EXCEPTION 'invalid readiness transition from building';
            END IF;
            IF NEW.status='passed' AND (
              NEW.payload_index_digest IS NULL OR NEW.provenance_digest IS NULL
              OR NEW.lifecycle_revision_digest IS NULL
              OR NEW.readiness_digest IS NULL OR NEW.signature IS NULL
              OR NEW.checked_at IS NULL OR cardinality(NEW.blocker_codes)<>0
            ) THEN
              RAISE EXCEPTION 'passed readiness requires a complete signed result';
            END IF;
          ELSIF OLD.status='passed' THEN
            IF NEW.status NOT IN ('stale','activated') THEN
              RAISE EXCEPTION 'invalid readiness transition from passed';
            END IF;
            IF ROW(
              OLD.generation_id, OLD.org_id, OLD.workspace_id, OLD.request_digest,
              OLD.physical_collection, OLD.collection_alias, OLD.schema_version,
              OLD.current_version_count, OLD.ready_version_count,
              OLD.projected_version_count, OLD.point_count,
              OLD.payload_index_digest, OLD.provenance_digest,
              OLD.lifecycle_revision_digest, OLD.grounding_policy_id,
              OLD.grounding_policy_version, OLD.calibration_hash,
              OLD.verifier_model_id, OLD.provider_preset_version,
              OLD.binding_revision, OLD.credential_fingerprint,
              OLD.readiness_digest, OLD.signature, OLD.blocker_codes,
              OLD.lease_owner, OLD.lease_expires_at, OLD.attempts,
              OLD.checked_at, OLD.expires_at, OLD.id, OLD.created_at
            ) IS DISTINCT FROM ROW(
              NEW.generation_id, NEW.org_id, NEW.workspace_id, NEW.request_digest,
              NEW.physical_collection, NEW.collection_alias, NEW.schema_version,
              NEW.current_version_count, NEW.ready_version_count,
              NEW.projected_version_count, NEW.point_count,
              NEW.payload_index_digest, NEW.provenance_digest,
              NEW.lifecycle_revision_digest, NEW.grounding_policy_id,
              NEW.grounding_policy_version, NEW.calibration_hash,
              NEW.verifier_model_id, NEW.provider_preset_version,
              NEW.binding_revision, NEW.credential_fingerprint,
              NEW.readiness_digest, NEW.signature, NEW.blocker_codes,
              NEW.lease_owner, NEW.lease_expires_at, NEW.attempts,
              NEW.checked_at, NEW.expires_at, NEW.id, NEW.created_at
            ) THEN
              RAISE EXCEPTION 'passed readiness snapshot and signed result are immutable';
            END IF;
            IF NEW.status='activated' AND (
              NEW.activated_at IS NULL OR NEW.activated_by IS NULL
            ) THEN
              RAISE EXCEPTION 'activated readiness requires actor and timestamp';
            END IF;
            IF NEW.status='stale' AND ROW(OLD.activated_at, OLD.activated_by)
               IS DISTINCT FROM ROW(NEW.activated_at, NEW.activated_by) THEN
              RAISE EXCEPTION 'stale readiness cannot carry activation fields';
            END IF;
          ELSE
            RAISE EXCEPTION 'invalid readiness status';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION openrag_protect_terminal_calibration() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.state IN ('passed','failed') THEN
            RAISE EXCEPTION 'terminal calibration run is immutable';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_readiness_terminal_immutable
        BEFORE UPDATE ON document_authority_readiness FOR EACH ROW
        EXECUTE FUNCTION openrag_protect_terminal_readiness();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_calibration_terminal_immutable
        BEFORE UPDATE ON grounding_calibration_runs FOR EACH ROW
        EXECUTE FUNCTION openrag_protect_terminal_calibration();
        """
    )
    op.execute(
        """
        CREATE FUNCTION openrag_protect_evidence_artifact() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          owning_org uuid;
          owning_version uuid;
          owning_state text;
          owning_provenance text;
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'immutable evidence artifact cannot be updated';
          END IF;
          owning_org := CASE WHEN TG_OP='DELETE' THEN OLD.org_id ELSE NEW.org_id END;
          owning_version := CASE WHEN TG_OP='DELETE' THEN OLD.document_version_id
                                 ELSE NEW.document_version_id END;
          SELECT state, provenance_state INTO owning_state, owning_provenance
          FROM document_versions
          WHERE org_id=owning_org AND id=owning_version
          FOR SHARE;
          IF NOT FOUND AND TG_OP='DELETE' AND pg_trigger_depth()>1 THEN
            -- A parent-version cascade is admitted only after the version's
            -- own BEFORE DELETE lifecycle trigger accepts a never-approved
            -- deletable state.
            RETURN OLD;
          END IF;
          IF NOT FOUND OR NOT (
            owning_state='processing' OR owning_provenance='building'
          ) THEN
            RAISE EXCEPTION 'evidence artifact mutation requires processing/building owner';
          END IF;
          IF TG_OP='DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    for table in (
        "document_blocks",
        "document_chunks",
        "document_chunk_blocks",
        "document_evidence_spans",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable "
            f"BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION openrag_protect_evidence_artifact();"
        )


def _drop_authority_triggers() -> None:
    for table in (
        "document_evidence_spans",
        "document_chunk_blocks",
        "document_chunks",
        "document_blocks",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_calibration_terminal_immutable ON grounding_calibration_runs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_readiness_terminal_immutable ON document_authority_readiness"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_citations_parent_final_state ON citations")
    op.execute("DROP TRIGGER IF EXISTS trg_messages_final_state ON messages")
    op.execute("DROP TRIGGER IF EXISTS trg_citations_validate_write ON citations")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_versions_delete_lifecycle "
        "ON document_versions"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_document_versions_immutable ON document_versions")
    op.execute("DROP FUNCTION IF EXISTS openrag_protect_evidence_artifact()")
    op.execute("DROP FUNCTION IF EXISTS openrag_protect_terminal_calibration()")
    op.execute("DROP FUNCTION IF EXISTS openrag_protect_terminal_readiness()")
    op.execute("DROP FUNCTION IF EXISTS openrag_validate_message_final_state()")
    op.execute("DROP FUNCTION IF EXISTS openrag_validate_citation_write()")
    op.execute("DROP FUNCTION IF EXISTS openrag_validate_document_version_delete()")
    op.execute("DROP FUNCTION IF EXISTS openrag_validate_document_version_update()")


def _downgrade_preflight(connection: sa.Connection) -> None:
    checks = (
        (
            "SELECT EXISTS(SELECT 1 FROM workspaces WHERE document_authority_enabled)",
            "enabled workspace",
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM document_versions GROUP BY document_id HAVING count(*)<>1)",
            "multiple versions",
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM citations WHERE verification_state<>'legacy_unverified')",
            "authority citation",
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM messages WHERE answer_status IN ('grounded','cited_conflict'))",
            "grounded message",
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM document_versions WHERE provenance_state NOT IN ('none','legacy_pending'))",
            "non-legacy provenance",
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM document_versions WHERE NOT ("
            "sequence=1 AND version_label='Legacy 1' AND version_key='legacy 1' "
            "AND parser_profile_version='legacy/parser-v1' "
            "AND ocr_profile_version='legacy/ocr-unknown-v1' "
            "AND chunking_profile_version='legacy/chunking-v1' "
            "AND embedding_profile_version='legacy/embedding-v1' "
            "AND index_profile_version='legacy/index-v1' "
            "AND ((state='approved' AND provenance_state='legacy_pending') "
            "OR (state='failed' AND provenance_state='none') "
            "OR (state='processing' AND provenance_state='none'))))",
            "sole version is not exact legacy",
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM document_blocks "
            "UNION ALL SELECT 1 FROM document_chunks "
            "UNION ALL SELECT 1 FROM document_chunk_blocks "
            "UNION ALL SELECT 1 FROM document_evidence_spans "
            "UNION ALL SELECT 1 FROM document_version_projections "
            "UNION ALL SELECT 1 FROM ingest_stage_attempts "
            "UNION ALL SELECT 1 FROM legacy_rebuild_scan_checkpoints)",
            "derived artifact",
        ),
        ("SELECT EXISTS(SELECT 1 FROM document_authority_readiness)", "readiness generation"),
        ("SELECT EXISTS(SELECT 1 FROM grounding_policies)", "grounding policy"),
        ("SELECT EXISTS(SELECT 1 FROM grounding_calibration_runs)", "calibration run"),
        (
            "SELECT EXISTS(SELECT 1 FROM outbox_events "
            "WHERE event_type LIKE 'document.version.%' UNION ALL "
            "SELECT 1 FROM inbox_events WHERE event_type LIKE 'document.version.%')",
            "document version event",
        ),
        (
            "SELECT EXISTS(SELECT 1 FROM ingest_jobs WHERE document_id IS NULL)",
            "version-only ingest job",
        ),
    )
    for query, label in checks:
        if connection.execute(sa.text(query)).scalar_one():
            raise RuntimeError(f"document authority downgrade preflight failed: {label}")
    connection.execute(
        sa.text(
            "UPDATE documents d SET filename=v.source_filename, mime=v.source_mime, "
            "size_bytes=v.source_size_bytes, content_hash=v.content_hash, "
            "storage_key=v.source_storage_key, page_count=v.source_page_count "
            "FROM document_versions v WHERE v.document_id=d.id"
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()
    _lock_legacy_tables(connection)
    _reject_invalid_legacy_citations(connection)
    _prepare_compatibility_tables()
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "grounding_policies",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("verifier_model_id", sa.Uuid(), nullable=False),
        sa.Column("binding_revision", sa.String(length=100), nullable=False),
        sa.Column("provider_preset_version", sa.String(length=100), nullable=False),
        sa.Column("credential_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("entailment_threshold", sa.Float(), nullable=False),
        sa.Column("calibration_dataset_version", sa.String(length=100), nullable=False),
        sa.Column("calibration_dataset_hash", sa.String(length=64), nullable=False),
        sa.Column("calibration_sample_count", sa.Integer(), nullable=False),
        sa.Column("measured_false_support_rate", sa.Float(), nullable=True),
        sa.Column("measured_false_refusal_rate", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "calibration_dataset_hash ~ '^[0-9a-f]{64}$'", name="ck_grounding_policies_dataset_hash"
        ),
        sa.CheckConstraint(
            "status IN ('draft','passed','active','retired')", name="ck_grounding_policies_status"
        ),
        sa.CheckConstraint(
            "calibration_sample_count >= 0", name="ck_grounding_policies_sample_count"
        ),
        sa.CheckConstraint(
            "char_length(binding_revision) BETWEEN 1 AND 100 AND char_length(credential_fingerprint) BETWEEN 1 AND 128",
            name="ck_grounding_policies_binding_snapshot",
        ),
        sa.CheckConstraint(
            "char_length(calibration_dataset_version) BETWEEN 1 AND 100",
            name="ck_grounding_policies_dataset_version",
        ),
        sa.CheckConstraint(
            "char_length(provider_preset_version) BETWEEN 1 AND 100",
            name="ck_grounding_policies_preset_version",
        ),
        sa.CheckConstraint(
            "entailment_threshold >= 0 AND entailment_threshold <= 1",
            name="ck_grounding_policies_entailment_threshold",
        ),
        sa.CheckConstraint(
            "measured_false_refusal_rate IS NULL OR (measured_false_refusal_rate >= 0 AND measured_false_refusal_rate <= 1)",
            name="ck_grounding_policies_false_refusal_rate",
        ),
        sa.CheckConstraint(
            "measured_false_support_rate IS NULL OR (measured_false_support_rate >= 0 AND measured_false_support_rate <= 1)",
            name="ck_grounding_policies_false_support_rate",
        ),
        sa.CheckConstraint("policy_version > 0", name="ck_grounding_policies_version"),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_grounding_policies_org_creator",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_grounding_policies_org_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["verifier_model_id"],
            ["models.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            "policy_version",
            "verifier_model_id",
            "calibration_dataset_hash",
            "provider_preset_version",
            "binding_revision",
            "credential_fingerprint",
            name="uq_grounding_policies_immutable_snapshot",
        ),
        sa.UniqueConstraint("org_id", "workspace_id", "id", name="uq_grounding_policies_scope_id"),
        sa.UniqueConstraint(
            "org_id", "workspace_id", "policy_version", name="uq_grounding_policies_scope_version"
        ),
    )
    op.create_index(
        op.f("ix_grounding_policies_org_id"), "grounding_policies", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_grounding_policies_status"), "grounding_policies", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_grounding_policies_verifier_model_id"),
        "grounding_policies",
        ["verifier_model_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_grounding_policies_workspace_id"),
        "grounding_policies",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_grounding_policies_active_workspace",
        "grounding_policies",
        ["org_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "legacy_rebuild_scan_checkpoints",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("cursor_document_version_id", sa.Uuid(), nullable=True),
        sa.Column("pass_number", sa.Integer(), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("emitted_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("pass_started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "pass_number >= 0 AND scanned_count >= 0 AND emitted_count >= 0 AND skipped_count >= 0",
            name="ck_legacy_rebuild_scan_checkpoints_counts",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_legacy_rebuild_scan_checkpoints_org_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "workspace_id", name="uq_legacy_rebuild_scan_checkpoints_workspace"
        ),
    )
    op.create_index(
        op.f("ix_legacy_rebuild_scan_checkpoints_org_id"),
        "legacy_rebuild_scan_checkpoints",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_legacy_rebuild_scan_checkpoints_workspace_id"),
        "legacy_rebuild_scan_checkpoints",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "document_authority_readiness",
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("physical_collection", sa.String(length=200), nullable=False),
        sa.Column("collection_alias", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("current_version_count", sa.Integer(), nullable=False),
        sa.Column("ready_version_count", sa.Integer(), nullable=False),
        sa.Column("projected_version_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("payload_index_digest", sa.String(length=64), nullable=True),
        sa.Column("provenance_digest", sa.String(length=64), nullable=True),
        sa.Column("lifecycle_revision_digest", sa.String(length=64), nullable=True),
        sa.Column("grounding_policy_id", sa.Uuid(), nullable=False),
        sa.Column("grounding_policy_version", sa.Integer(), nullable=False),
        sa.Column("calibration_hash", sa.String(length=64), nullable=False),
        sa.Column("verifier_model_id", sa.Uuid(), nullable=False),
        sa.Column("provider_preset_version", sa.String(length=100), nullable=False),
        sa.Column("binding_revision", sa.String(length=100), nullable=False),
        sa.Column("credential_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("readiness_digest", sa.String(length=64), nullable=True),
        sa.Column("signature", sa.String(length=128), nullable=True),
        sa.Column("blocker_codes", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "calibration_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_calibration_hash",
        ),
        sa.CheckConstraint(
            "lifecycle_revision_digest IS NULL OR lifecycle_revision_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_lifecycle_digest",
        ),
        sa.CheckConstraint(
            "payload_index_digest IS NULL OR payload_index_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_payload_digest",
        ),
        sa.CheckConstraint(
            "provenance_digest IS NULL OR provenance_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_provenance_digest",
        ),
        sa.CheckConstraint(
            "readiness_digest IS NULL OR readiness_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_digest",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_request_digest",
        ),
        sa.CheckConstraint(
            "signature IS NULL OR signature ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_signature",
        ),
        sa.CheckConstraint(
            "status IN ('building','passed','stale','failed','activated')",
            name="ck_document_authority_readiness_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_document_authority_readiness_attempts"),
        sa.CheckConstraint(
            "cardinality(blocker_codes) <= 32", name="ck_document_authority_readiness_blocker_count"
        ),
        sa.CheckConstraint(
            "char_length(physical_collection) BETWEEN 1 AND 200 AND char_length(collection_alias) BETWEEN 1 AND 200",
            name="ck_document_authority_readiness_collection_names",
        ),
        sa.CheckConstraint(
            "current_version_count >= 0 AND ready_version_count >= 0 AND ready_version_count <= current_version_count AND projected_version_count >= 0 AND projected_version_count <= current_version_count AND point_count >= 0",
            name="ck_document_authority_readiness_counts",
        ),
        sa.CheckConstraint(
            "grounding_policy_version > 0", name="ck_document_authority_readiness_policy_snapshot"
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_document_authority_readiness_schema"),
        sa.ForeignKeyConstraint(
            ["org_id", "activated_by"],
            ["users.org_id", "users.id"],
            name="fk_document_authority_readiness_org_activator",
        ),
        sa.ForeignKeyConstraint(
            [
                "org_id",
                "workspace_id",
                "grounding_policy_id",
                "grounding_policy_version",
                "verifier_model_id",
                "calibration_hash",
                "provider_preset_version",
                "binding_revision",
                "credential_fingerprint",
            ],
            [
                "grounding_policies.org_id",
                "grounding_policies.workspace_id",
                "grounding_policies.id",
                "grounding_policies.policy_version",
                "grounding_policies.verifier_model_id",
                "grounding_policies.calibration_dataset_hash",
                "grounding_policies.provider_preset_version",
                "grounding_policies.binding_revision",
                "grounding_policies.credential_fingerprint",
            ],
            name="fk_document_authority_readiness_policy_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_document_authority_readiness_org_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["verifier_model_id"],
            ["models.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "generation_id",
            name="uq_document_authority_readiness_generation",
        ),
    )
    op.create_index(
        op.f("ix_document_authority_readiness_org_id"),
        "document_authority_readiness",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_authority_readiness_status"),
        "document_authority_readiness",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_authority_readiness_workspace_id"),
        "document_authority_readiness",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "document_versions",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=200), nullable=False),
        sa.Column("version_key", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=500), nullable=True),
        sa.Column("source_mime", sa.String(length=255), nullable=True),
        sa.Column("source_size_bytes", sa.Integer(), nullable=True),
        sa.Column("source_storage_key", sa.String(length=1024), nullable=True),
        sa.Column("source_page_count", sa.Integer(), nullable=True),
        sa.Column("revision_date", sa.DateTime(), nullable=True),
        sa.Column("parser_profile_version", sa.String(length=100), nullable=False),
        sa.Column("ocr_profile_version", sa.String(length=100), nullable=False),
        sa.Column("chunking_profile_version", sa.String(length=100), nullable=False),
        sa.Column("embedding_profile_version", sa.String(length=100), nullable=False),
        sa.Column("index_profile_version", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("provenance_state", sa.String(), nullable=False),
        sa.Column("lifecycle_revision", sa.Integer(), nullable=False),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("effective_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("rejected_by", sa.Uuid(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(), nullable=True),
        sa.Column("obsolete_by", sa.Uuid(), nullable=True),
        sa.Column("obsolete_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("decision_at", sa.DateTime(), nullable=True),
        sa.Column("processing_error_code", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(sequence = 1 AND version_label = 'Legacy 1' AND version_key = 'legacy 1' AND parser_profile_version = 'legacy/parser-v1' AND ocr_profile_version = 'legacy/ocr-unknown-v1' AND chunking_profile_version = 'legacy/chunking-v1' AND embedding_profile_version = 'legacy/embedding-v1' AND index_profile_version = 'legacy/index-v1' AND ((state = 'approved' AND provenance_state = 'legacy_pending') OR (state = 'failed' AND provenance_state = 'none') OR (state = 'processing' AND provenance_state = 'none'))) OR (version_label <> 'Legacy 1' AND version_key <> 'legacy 1' AND provenance_state <> 'legacy_pending' AND parser_profile_version <> 'legacy/parser-v1' AND ocr_profile_version <> 'legacy/ocr-unknown-v1' AND chunking_profile_version <> 'legacy/chunking-v1' AND embedding_profile_version <> 'legacy/embedding-v1' AND index_profile_version <> 'legacy/index-v1')",
            name="ck_document_versions_exact_legacy_contract",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_document_versions_content_hash_sha256"
        ),
        sa.CheckConstraint(
            "provenance_state IN ('none','legacy_pending','building','ready','failed')",
            name="ck_document_versions_provenance_state",
        ),
        sa.CheckConstraint(
            "source_page_count IS NOT NULL OR (sequence = 1 AND version_label = 'Legacy 1' AND version_key = 'legacy 1' AND ((state = 'approved' AND provenance_state = 'legacy_pending') OR (state = 'failed' AND provenance_state = 'none') OR (state = 'processing' AND provenance_state = 'none')))",
            name="ck_document_versions_page_count_or_exact_legacy",
        ),
        sa.CheckConstraint(
            "state IN ('draft','processing','review','approved','rejected','superseded','obsolete','failed')",
            name="ck_document_versions_state",
        ),
        sa.CheckConstraint(
            "parser_profile_version IS NOT NULL AND ocr_profile_version IS NOT NULL AND chunking_profile_version IS NOT NULL AND embedding_profile_version IS NOT NULL AND index_profile_version IS NOT NULL AND char_length(parser_profile_version) BETWEEN 1 AND 100 AND char_length(ocr_profile_version) BETWEEN 1 AND 100 AND char_length(chunking_profile_version) BETWEEN 1 AND 100 AND char_length(embedding_profile_version) BETWEEN 1 AND 100 AND char_length(index_profile_version) BETWEEN 1 AND 100",
            name="ck_document_versions_profile_snapshot_complete",
        ),
        sa.CheckConstraint(
            "lifecycle_revision >= 1", name="ck_document_versions_lifecycle_revision_positive"
        ),
        sa.CheckConstraint("sequence > 0", name="ck_document_versions_sequence_positive"),
        sa.CheckConstraint(
            "source_filename IS NOT NULL AND source_mime IS NOT NULL AND source_size_bytes IS NOT NULL AND source_storage_key IS NOT NULL",
            name="ck_document_versions_source_identity_complete",
        ),
        sa.CheckConstraint(
            "source_page_count IS NULL OR source_page_count > 0",
            name="ck_document_versions_source_page_count",
        ),
        sa.CheckConstraint(
            "source_size_bytes IS NULL OR source_size_bytes >= 0",
            name="ck_document_versions_source_size",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "superseded_by_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_document_versions_same_document_successor",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "approved_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_approver",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_creator",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "obsolete_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_obsolete_actor",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "rejected_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_rejector",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_id"],
            ["documents.org_id", "documents.workspace_id", "documents.id"],
            name="fk_document_versions_org_workspace_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "content_hash", name="uq_document_versions_document_hash"
        ),
        sa.UniqueConstraint("document_id", "id", name="uq_document_versions_document_id"),
        sa.UniqueConstraint(
            "document_id", "sequence", name="uq_document_versions_document_sequence"
        ),
        sa.UniqueConstraint("document_id", "version_key", name="uq_document_versions_document_key"),
        sa.UniqueConstraint(
            "org_id",
            "document_id",
            "id",
            "version_label",
            name="uq_document_versions_org_document_id_label",
        ),
        sa.UniqueConstraint(
            "org_id", "document_id", "id", name="uq_document_versions_org_document_id"
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_document_versions_org_id"),
        sa.UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_document_versions_org_workspace_id"
        ),
    )
    op.create_index(
        op.f("ix_document_versions_document_id"), "document_versions", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_document_versions_org_id"), "document_versions", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_document_versions_provenance_state"),
        "document_versions",
        ["provenance_state"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_versions_state"), "document_versions", ["state"], unique=False
    )
    op.create_index(
        op.f("ix_document_versions_workspace_id"),
        "document_versions",
        ["workspace_id"],
        unique=False,
    )
    _backfill_versions_and_jobs(connection)
    op.create_table(
        "grounding_calibration_runs",
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=False),
        sa.Column("requested_binding_revision", sa.String(length=100), nullable=False),
        sa.Column("requested_preset_version", sa.String(length=100), nullable=False),
        sa.Column("requested_credential_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("checkpoint", sa.String(length=128), nullable=True),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("supported_count", sa.Integer(), nullable=False),
        sa.Column("refused_count", sa.Integer(), nullable=False),
        sa.Column("false_support_rate", sa.Float(), nullable=True),
        sa.Column("false_refusal_rate", sa.Float(), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "idempotency_digest ~ '^[0-9a-f]{64}$'",
            name="ck_grounding_calibration_runs_idempotency_digest",
        ),
        sa.CheckConstraint(
            "result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_grounding_calibration_runs_result_digest",
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','passed','failed')",
            name="ck_grounding_calibration_runs_state",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_grounding_calibration_runs_attempts"),
        sa.CheckConstraint(
            "char_length(requested_binding_revision) BETWEEN 1 AND 100 AND char_length(requested_preset_version) BETWEEN 1 AND 100 AND char_length(requested_credential_fingerprint) BETWEEN 1 AND 128",
            name="ck_grounding_calibration_runs_binding_snapshot",
        ),
        sa.CheckConstraint(
            "false_refusal_rate IS NULL OR (false_refusal_rate >= 0 AND false_refusal_rate <= 1)",
            name="ck_grounding_calibration_runs_false_refusal_rate",
        ),
        sa.CheckConstraint(
            "false_support_rate IS NULL OR (false_support_rate >= 0 AND false_support_rate <= 1)",
            name="ck_grounding_calibration_runs_false_support_rate",
        ),
        sa.CheckConstraint(
            "sample_count >= 0 AND supported_count >= 0 AND refused_count >= 0 AND supported_count + refused_count = sample_count",
            name="ck_grounding_calibration_runs_counts",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "policy_id"],
            [
                "grounding_policies.org_id",
                "grounding_policies.workspace_id",
                "grounding_policies.id",
            ],
            name="fk_grounding_calibration_runs_scope_policy",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_grounding_calibration_runs_org_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "workspace_id", "id", name="uq_grounding_calibration_runs_scope_id"
        ),
        sa.UniqueConstraint(
            "org_id",
            "workspace_id",
            "policy_id",
            "generation_id",
            name="uq_grounding_calibration_runs_generation",
        ),
    )
    op.create_index(
        op.f("ix_grounding_calibration_runs_org_id"),
        "grounding_calibration_runs",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_grounding_calibration_runs_policy_id"),
        "grounding_calibration_runs",
        ["policy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_grounding_calibration_runs_state"),
        "grounding_calibration_runs",
        ["state"],
        unique=False,
    )
    op.create_index(
        op.f("ix_grounding_calibration_runs_workspace_id"),
        "grounding_calibration_runs",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "document_blocks",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_block_id", sa.Uuid(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("locator_kind", sa.String(length=32), nullable=False),
        sa.Column("locator_label", sa.String(length=200), nullable=False),
        sa.Column("block_type", sa.String(length=50), nullable=False),
        sa.Column("section_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_coordinates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extraction_method", sa.String(length=50), nullable=False),
        sa.Column("ocr_profile_version", sa.String(length=100), nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_document_blocks_content_hash"
        ),
        sa.CheckConstraint(
            "source_coordinates IS NULL OR (jsonb_typeof(source_coordinates) = 'object' AND pg_column_size(source_coordinates) <= 8192)",
            name="ck_document_blocks_source_coordinates",
        ),
        sa.CheckConstraint(
            'jsonb_typeof(section_path) = \'array\' AND jsonb_array_length(section_path) BETWEEN 1 AND 8 AND pg_column_size(section_path) <= 4096 AND jsonb_array_length(jsonb_path_query_array(section_path, \'$[*] ? (@.type() == "string" && @ like_regex "^.{1,200}$" flag "s")\')) = jsonb_array_length(section_path)',
            name="ck_document_blocks_section_path",
        ),
        sa.CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_document_blocks_ocr_confidence",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_document_blocks_ordinal"),
        sa.CheckConstraint("page_number > 0", name="ck_document_blocks_page_positive"),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id", "parent_block_id"],
            ["document_blocks.org_id", "document_blocks.document_version_id", "document_blocks.id"],
            name="fk_document_blocks_same_version_parent",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.id"],
            name="fk_document_blocks_org_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_blocks_version_ordinal"
        ),
        sa.UniqueConstraint(
            "org_id", "document_version_id", "id", name="uq_document_blocks_org_version_id"
        ),
    )
    op.create_index(
        op.f("ix_document_blocks_document_version_id"),
        "document_blocks",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(op.f("ix_document_blocks_org_id"), "document_blocks", ["org_id"], unique=False)
    op.create_table(
        "document_chunks",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("section_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("chunking_profile_version", sa.String(length=100), nullable=False),
        sa.Column("embedding_profile_version", sa.String(length=100), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_document_chunks_content_hash"
        ),
        sa.CheckConstraint(
            "char_length(chunking_profile_version) BETWEEN 1 AND 100 AND char_length(embedding_profile_version) BETWEEN 1 AND 100",
            name="ck_document_chunks_profile_snapshot",
        ),
        sa.CheckConstraint(
            'jsonb_typeof(section_path) = \'array\' AND jsonb_array_length(section_path) BETWEEN 1 AND 8 AND pg_column_size(section_path) <= 4096 AND jsonb_array_length(jsonb_path_query_array(section_path, \'$[*] ? (@.type() == "string" && @ like_regex "^.{1,200}$" flag "s")\')) = jsonb_array_length(section_path)',
            name="ck_document_chunks_section_path",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal"),
        sa.CheckConstraint(
            "page_end > 0 AND page_end >= page_start", name="ck_document_chunks_page_range"
        ),
        sa.CheckConstraint("page_start > 0", name="ck_document_chunks_page_start"),
        sa.CheckConstraint("token_count >= 0", name="ck_document_chunks_token_count"),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id", "parent_chunk_id"],
            ["document_chunks.org_id", "document_chunks.document_version_id", "document_chunks.id"],
            name="fk_document_chunks_same_version_parent",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.id"],
            name="fk_document_chunks_org_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_chunks_version_ordinal"
        ),
        sa.UniqueConstraint(
            "org_id", "document_version_id", "id", name="uq_document_chunks_org_version_id"
        ),
    )
    op.create_index(
        op.f("ix_document_chunks_document_version_id"),
        "document_chunks",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(op.f("ix_document_chunks_org_id"), "document_chunks", ["org_id"], unique=False)
    op.create_table(
        "document_version_projections",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("is_current_eligible", sa.Boolean(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "applied_revision >= 1", name="ck_document_version_projections_applied_revision"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.workspace_id", "document_versions.id"],
            name="fk_document_version_projections_org_workspace_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "document_version_id", name="uq_document_version_projections_version"
        ),
    )
    op.create_index(
        op.f("ix_document_version_projections_document_version_id"),
        "document_version_projections",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_version_projections_is_current_eligible"),
        "document_version_projections",
        ["is_current_eligible"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_version_projections_org_id"),
        "document_version_projections",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_version_projections_workspace_id"),
        "document_version_projections",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "ingest_stage_attempts",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_kind", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("checkpoint", sa.String(length=128), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_ingest_stage_attempts_attempts"),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.workspace_id", "document_versions.id"],
            name="fk_ingest_stage_attempts_org_workspace_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "pipeline_kind",
            "stage",
            "checkpoint",
            name="uq_ingest_stage_attempts_checkpoint",
        ),
    )
    op.create_index(
        op.f("ix_ingest_stage_attempts_document_version_id"),
        "ingest_stage_attempts",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ingest_stage_attempts_org_id"), "ingest_stage_attempts", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_ingest_stage_attempts_state"), "ingest_stage_attempts", ["state"], unique=False
    )
    op.create_index(
        op.f("ix_ingest_stage_attempts_workspace_id"),
        "ingest_stage_attempts",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "document_chunk_blocks",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("block_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_document_chunk_blocks_position"),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id", "block_id"],
            ["document_blocks.org_id", "document_blocks.document_version_id", "document_blocks.id"],
            name="fk_document_chunk_blocks_same_version_block",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id", "chunk_id"],
            ["document_chunks.org_id", "document_chunks.document_version_id", "document_chunks.id"],
            name="fk_document_chunk_blocks_same_version_chunk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "block_id", name="uq_document_chunk_blocks_membership"),
        sa.UniqueConstraint("chunk_id", "position", name="uq_document_chunk_blocks_position"),
    )
    op.create_index(
        op.f("ix_document_chunk_blocks_block_id"),
        "document_chunk_blocks",
        ["block_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_chunk_blocks_chunk_id"),
        "document_chunk_blocks",
        ["chunk_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_chunk_blocks_document_version_id"),
        "document_chunk_blocks",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_chunk_blocks_org_id"), "document_chunk_blocks", ["org_id"], unique=False
    )
    op.create_table(
        "document_evidence_spans",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("locator_kind", sa.String(length=32), nullable=False),
        sa.Column("locator_label", sa.String(length=200), nullable=False),
        sa.Column("section_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("artifact_byte_start", sa.Integer(), nullable=False),
        sa.Column("artifact_byte_end", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_document_evidence_spans_content_hash"
        ),
        sa.CheckConstraint(
            "artifact_byte_start >= 0 AND artifact_byte_end >= artifact_byte_start",
            name="ck_document_evidence_spans_artifact_range",
        ),
        sa.CheckConstraint(
            'jsonb_typeof(section_path) = \'array\' AND jsonb_array_length(section_path) BETWEEN 1 AND 8 AND pg_column_size(section_path) <= 4096 AND jsonb_array_length(jsonb_path_query_array(section_path, \'$[*] ? (@.type() == "string" && @ like_regex "^.{1,200}$" flag "s")\')) = jsonb_array_length(section_path)',
            name="ck_document_evidence_spans_section_path",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_document_evidence_spans_ordinal"),
        sa.CheckConstraint("page_number > 0", name="ck_document_evidence_spans_page_positive"),
        sa.CheckConstraint("token_count >= 0", name="ck_document_evidence_spans_token_count"),
        sa.ForeignKeyConstraint(
            ["org_id", "document_version_id", "chunk_id"],
            ["document_chunks.org_id", "document_chunks.document_version_id", "document_chunks.id"],
            name="fk_document_evidence_spans_same_version_chunk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_evidence_spans_version_ordinal"
        ),
        sa.UniqueConstraint(
            "org_id", "document_version_id", "id", name="uq_document_evidence_spans_org_version_id"
        ),
    )
    op.create_index(
        op.f("ix_document_evidence_spans_chunk_id"),
        "document_evidence_spans",
        ["chunk_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_evidence_spans_document_version_id"),
        "document_evidence_spans",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_evidence_spans_org_id"),
        "document_evidence_spans",
        ["org_id"],
        unique=False,
    )
    _expand_and_backfill_citations(connection)
    _finalize_citations()
    _install_authority_triggers()
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    connection = op.get_bind()
    _downgrade_preflight(connection)
    _drop_authority_triggers()
    op.drop_constraint("ck_citations_page_positive", "citations", type_="check")
    op.drop_constraint("ck_messages_role", "messages", type_="check")
    for constraint in (
        "fk_citations_org_document_version",
        "fk_citations_org_version_evidence_span",
        "fk_citations_org_workspace_document",
        "fk_citations_org_workspace_message",
    ):
        op.drop_constraint(constraint, "citations", type_="foreignkey")
    op.drop_constraint(
        "fk_document_versions_org_workspace_document",
        "document_versions",
        type_="foreignkey",
    )
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("workspaces", "document_authority_enabled")
    op.drop_index(op.f("ix_outbox_events_lease_owner"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_lease_expires_at"), table_name="outbox_events")
    op.drop_column("outbox_events", "lease_expires_at")
    op.drop_column("outbox_events", "lease_owner")
    op.drop_index(op.f("ix_inbox_events_event_type"), table_name="inbox_events")
    op.drop_constraint(
        "ck_inbox_events_event_type_bounded", "inbox_events", type_="check"
    )
    op.drop_column("inbox_events", "event_type")
    op.drop_column("models", "provider_preset_version")
    op.drop_column("models", "supports_verifier")
    op.drop_column("models", "supports_structured_json")
    op.drop_column("models", "supports_chat_completion")
    op.drop_constraint("fk_messages_same_chat_parent", "messages", type_="foreignkey")
    op.drop_constraint("fk_messages_org_workspace_chat", "messages", type_="foreignkey")
    op.create_foreign_key(
        op.f("messages_parent_message_id_fkey"),
        "messages",
        "messages",
        ["parent_message_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_messages_org_workspace_id", "messages", type_="unique")
    op.drop_constraint("uq_messages_chat_id", "messages", type_="unique")
    op.drop_index(op.f("ix_messages_workspace_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_org_id"), table_name="messages")
    op.drop_column("messages", "credential_fingerprint")
    op.drop_column("messages", "binding_revision")
    op.drop_column("messages", "provider_preset_version")
    op.drop_column("messages", "prompt_contract_version")
    op.drop_column("messages", "verifier_model_id")
    op.drop_column("messages", "grounding_policy_version")
    op.drop_column("messages", "grounding_policy_id")
    op.drop_column("messages", "refusal_reason")
    op.drop_column("messages", "answer_status")
    op.drop_column("messages", "workspace_id")
    op.drop_column("messages", "org_id")
    op.drop_constraint("fk_ingest_jobs_org_document_version", "ingest_jobs", type_="foreignkey")
    op.drop_index(op.f("ix_ingest_jobs_org_id"), table_name="ingest_jobs")
    op.drop_index(op.f("ix_ingest_jobs_document_version_id"), table_name="ingest_jobs")
    op.alter_column("ingest_jobs", "document_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("ingest_jobs", "document_version_id")
    op.drop_column("ingest_jobs", "org_id")
    op.drop_constraint("fk_documents_org_owner", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_org_creator", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_org_workspace", "documents", type_="foreignkey")
    op.create_foreign_key(
        op.f("documents_workspace_id_fkey"), "documents", "workspaces", ["workspace_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("documents_created_by_fkey"), "documents", "users", ["created_by"], ["id"]
    )
    op.drop_constraint("uq_documents_org_workspace_id", "documents", type_="unique")
    op.drop_constraint(
        "uq_documents_org_workspace_external_identifier", "documents", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_documents_workspace_hash"),
        "documents",
        ["workspace_id", "content_hash"],
        postgresql_nulls_not_distinct=False,
    )
    op.alter_column("documents", "storage_key", existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column("documents", "status", existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column("documents", "content_hash", existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column("documents", "size_bytes", existing_type=sa.INTEGER(), nullable=False)
    op.alter_column("documents", "mime", existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column("documents", "filename", existing_type=sa.VARCHAR(), nullable=False)
    op.drop_column("documents", "owner_id")
    op.drop_column("documents", "acl_policy")
    op.drop_column("documents", "external_identifier")
    op.drop_column("documents", "document_type")
    op.drop_column("documents", "department")
    op.drop_column("documents", "name")
    op.drop_index(op.f("ix_citations_workspace_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_org_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_evidence_span_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_document_version_id"), table_name="citations")
    op.drop_column("citations", "credential_fingerprint")
    op.drop_column("citations", "binding_revision")
    op.drop_column("citations", "provider_preset_version")
    op.drop_column("citations", "verifier_model_id")
    op.drop_column("citations", "grounding_policy_version")
    op.drop_column("citations", "grounding_policy_id")
    op.drop_column("citations", "prompt_contract_version")
    op.drop_column("citations", "verification_state")
    op.drop_column("citations", "claim_ids")
    op.drop_column("citations", "claim_id")
    op.drop_column("citations", "rerank_score")
    op.drop_column("citations", "fused_score")
    op.drop_column("citations", "sparse_score")
    op.drop_column("citations", "dense_score")
    op.drop_column("citations", "content_hash")
    op.drop_column("citations", "locator_label")
    op.drop_column("citations", "locator_kind")
    op.drop_column("citations", "section_path")
    op.drop_column("citations", "section_label")
    op.drop_column("citations", "version_label")
    op.drop_column("citations", "document_name")
    op.drop_column("citations", "evidence_span_id")
    op.drop_column("citations", "document_version_id")
    op.drop_column("citations", "workspace_id")
    op.drop_column("citations", "org_id")
    op.drop_constraint("fk_chats_org_workspace", "chats", type_="foreignkey")
    op.drop_constraint("fk_chats_org_user", "chats", type_="foreignkey")
    op.create_foreign_key(
        op.f("chats_workspace_id_fkey"), "chats", "workspaces", ["workspace_id"], ["id"]
    )
    op.create_foreign_key(op.f("chats_user_id_fkey"), "chats", "users", ["user_id"], ["id"])
    op.drop_constraint("uq_chats_org_workspace_id", "chats", type_="unique")
    op.drop_index(op.f("ix_document_evidence_spans_org_id"), table_name="document_evidence_spans")
    op.drop_index(
        op.f("ix_document_evidence_spans_document_version_id"), table_name="document_evidence_spans"
    )
    op.drop_index(op.f("ix_document_evidence_spans_chunk_id"), table_name="document_evidence_spans")
    op.drop_table("document_evidence_spans")
    op.drop_index(op.f("ix_document_chunk_blocks_org_id"), table_name="document_chunk_blocks")
    op.drop_index(
        op.f("ix_document_chunk_blocks_document_version_id"), table_name="document_chunk_blocks"
    )
    op.drop_index(op.f("ix_document_chunk_blocks_chunk_id"), table_name="document_chunk_blocks")
    op.drop_index(op.f("ix_document_chunk_blocks_block_id"), table_name="document_chunk_blocks")
    op.drop_table("document_chunk_blocks")
    op.drop_index(op.f("ix_ingest_stage_attempts_workspace_id"), table_name="ingest_stage_attempts")
    op.drop_index(op.f("ix_ingest_stage_attempts_state"), table_name="ingest_stage_attempts")
    op.drop_index(op.f("ix_ingest_stage_attempts_org_id"), table_name="ingest_stage_attempts")
    op.drop_index(
        op.f("ix_ingest_stage_attempts_document_version_id"), table_name="ingest_stage_attempts"
    )
    op.drop_table("ingest_stage_attempts")
    op.drop_index(
        op.f("ix_document_version_projections_workspace_id"),
        table_name="document_version_projections",
    )
    op.drop_index(
        op.f("ix_document_version_projections_org_id"), table_name="document_version_projections"
    )
    op.drop_index(
        op.f("ix_document_version_projections_is_current_eligible"),
        table_name="document_version_projections",
    )
    op.drop_index(
        op.f("ix_document_version_projections_document_version_id"),
        table_name="document_version_projections",
    )
    op.drop_table("document_version_projections")
    op.drop_index(op.f("ix_document_chunks_org_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_document_version_id"), table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index(op.f("ix_document_blocks_org_id"), table_name="document_blocks")
    op.drop_index(op.f("ix_document_blocks_document_version_id"), table_name="document_blocks")
    op.drop_table("document_blocks")
    op.drop_index(
        op.f("ix_grounding_calibration_runs_workspace_id"), table_name="grounding_calibration_runs"
    )
    op.drop_index(
        op.f("ix_grounding_calibration_runs_state"), table_name="grounding_calibration_runs"
    )
    op.drop_index(
        op.f("ix_grounding_calibration_runs_policy_id"), table_name="grounding_calibration_runs"
    )
    op.drop_index(
        op.f("ix_grounding_calibration_runs_org_id"), table_name="grounding_calibration_runs"
    )
    op.drop_table("grounding_calibration_runs")
    op.drop_index(op.f("ix_document_versions_workspace_id"), table_name="document_versions")
    op.drop_index(op.f("ix_document_versions_state"), table_name="document_versions")
    op.drop_index(op.f("ix_document_versions_provenance_state"), table_name="document_versions")
    op.drop_index(op.f("ix_document_versions_org_id"), table_name="document_versions")
    op.drop_index(op.f("ix_document_versions_document_id"), table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index(
        op.f("ix_document_authority_readiness_workspace_id"),
        table_name="document_authority_readiness",
    )
    op.drop_index(
        op.f("ix_document_authority_readiness_status"), table_name="document_authority_readiness"
    )
    op.drop_index(
        op.f("ix_document_authority_readiness_org_id"), table_name="document_authority_readiness"
    )
    op.drop_table("document_authority_readiness")
    op.drop_index(
        op.f("ix_legacy_rebuild_scan_checkpoints_workspace_id"),
        table_name="legacy_rebuild_scan_checkpoints",
    )
    op.drop_index(
        op.f("ix_legacy_rebuild_scan_checkpoints_org_id"),
        table_name="legacy_rebuild_scan_checkpoints",
    )
    op.drop_table("legacy_rebuild_scan_checkpoints")
    op.drop_index(
        "uq_grounding_policies_active_workspace",
        table_name="grounding_policies",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(op.f("ix_grounding_policies_workspace_id"), table_name="grounding_policies")
    op.drop_index(op.f("ix_grounding_policies_verifier_model_id"), table_name="grounding_policies")
    op.drop_index(op.f("ix_grounding_policies_status"), table_name="grounding_policies")
    op.drop_index(op.f("ix_grounding_policies_org_id"), table_name="grounding_policies")
    op.drop_table("grounding_policies")
    # ### end Alembic commands ###
