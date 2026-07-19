"""restartable governed source deletion

Revision ID: 4b8e0f7a3c21
Revises: 9d2c7a4e1f60
Create Date: 2026-07-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b8e0f7a3c21"
down_revision: str | Sequence[str] | None = "9d2c7a4e1f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _install_deletion_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION openrag_validate_source_deletion_marker() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.legacy_approval_backfilled IS DISTINCT FROM OLD.legacy_approval_backfilled THEN
            RAISE EXCEPTION 'legacy approval backfill provenance is immutable';
          END IF;

          IF NEW.id = NEW.document_id
             AND NEW.sequence = 1
             AND NEW.version_label = 'Legacy 1'
             AND NEW.version_key = 'legacy 1'
             AND NEW.state IS DISTINCT FROM OLD.state THEN
            IF NEW.lifecycle_revision <= OLD.lifecycle_revision THEN
              RAISE EXCEPTION 'legacy lifecycle transition requires lifecycle revision advancement';
            END IF;
            IF OLD.state = 'approved' AND NEW.state = 'failed' THEN
              RAISE EXCEPTION 'approved legacy version cannot regress to failed';
            END IF;
          END IF;

          IF OLD.source_delete_requested_at IS NOT NULL THEN
            IF NEW.source_delete_requested_at IS DISTINCT FROM OLD.source_delete_requested_at
               OR NEW.source_delete_requested_by IS DISTINCT FROM OLD.source_delete_requested_by
               OR (OLD.source_deleted_at IS NOT NULL
                   AND NEW.source_deleted_at IS DISTINCT FROM OLD.source_deleted_at) THEN
              RAISE EXCEPTION 'source deletion marker is immutable';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state
               OR NEW.provenance_state IS DISTINCT FROM OLD.provenance_state THEN
              RAISE EXCEPTION 'deletion-requested document version is immutable';
            END IF;
          END IF;

          IF NEW.source_delete_requested_at IS NOT NULL THEN
            IF NEW.state NOT IN ('draft','rejected','failed') THEN
              RAISE EXCEPTION 'source deletion request requires never-approved state';
            END IF;
            IF NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL
               OR EXISTS (
                 SELECT 1 FROM document_version_decision_records decision
                 WHERE decision.org_id=NEW.org_id
                   AND decision.document_version_id=NEW.id
                   AND decision.decision IN ('approved','superseded','obsolete')
               ) THEN
              RAISE EXCEPTION 'source deletion request cannot erase governed history';
            END IF;
            IF NEW.source_deleted_at IS NOT NULL
               AND OLD.source_delete_requested_at IS NULL THEN
              RAISE EXCEPTION 'source deletion must finalize after a committed request';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_versions_source_deletion
        BEFORE UPDATE ON document_versions FOR EACH ROW
        EXECUTE FUNCTION openrag_validate_source_deletion_marker();
        """
    )
    op.execute(
        """
        CREATE FUNCTION openrag_validate_decision_insert_against_deletion()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE deletion_requested_at timestamp;
        BEGIN
          SELECT source_delete_requested_at INTO deletion_requested_at
          FROM document_versions
          WHERE org_id=NEW.org_id AND id=NEW.document_version_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'decision version not found';
          END IF;
          IF deletion_requested_at IS NOT NULL THEN
            RAISE EXCEPTION 'decision cannot be appended after deletion request';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_version_decision_insert_deletion_guard
        BEFORE INSERT ON document_version_decision_records FOR EACH ROW
        EXECUTE FUNCTION openrag_validate_decision_insert_against_deletion();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION openrag_validate_document_version_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.state NOT IN ('draft','rejected','failed')
             OR OLD.source_delete_requested_at IS NULL
             OR OLD.source_delete_requested_by IS NULL
             OR OLD.source_deleted_at IS NULL THEN
            RAISE EXCEPTION 'document version deletion requires completed deletion request';
          END IF;
          IF OLD.approved_by IS NOT NULL OR OLD.approved_at IS NOT NULL
             OR EXISTS (
               SELECT 1 FROM document_version_decision_records decision
               WHERE decision.org_id=OLD.org_id
                 AND decision.document_version_id=OLD.id
                 AND decision.decision IN ('approved','superseded','obsolete')
             ) THEN
            RAISE EXCEPTION 'governed document version history cannot be deleted';
          END IF;
          RETURN OLD;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION openrag_protect_evidence_artifact() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          owning_org uuid;
          owning_version uuid;
          owning_state text;
          owning_provenance text;
          deletion_requested_at timestamp;
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'immutable evidence artifact cannot be updated';
          END IF;
          owning_org := CASE WHEN TG_OP='DELETE' THEN OLD.org_id ELSE NEW.org_id END;
          owning_version := CASE WHEN TG_OP='DELETE' THEN OLD.document_version_id
                                 ELSE NEW.document_version_id END;
          SELECT state, provenance_state, source_delete_requested_at
          INTO owning_state, owning_provenance, deletion_requested_at
          FROM document_versions
          WHERE org_id=owning_org AND id=owning_version
          FOR SHARE;
          IF NOT FOUND AND TG_OP='DELETE' AND pg_trigger_depth()>1 THEN
            RETURN OLD;
          END IF;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'evidence artifact owner not found';
          END IF;
          IF TG_OP='DELETE' AND deletion_requested_at IS NOT NULL
             AND owning_state IN ('draft','rejected','failed') THEN
            RETURN OLD;
          END IF;
          IF owning_provenance='ready' OR NOT (
            owning_state='processing' AND owning_provenance='building'
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


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column(
            "legacy_approval_backfilled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column("source_delete_requested_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("source_delete_requested_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("source_deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_document_versions_org_source_delete_requester",
        "document_versions",
        "users",
        ["org_id", "source_delete_requested_by"],
        ["org_id", "id"],
    )
    op.create_check_constraint(
        "ck_document_versions_source_deletion_markers",
        "document_versions",
        "(source_delete_requested_at IS NULL "
        "AND source_delete_requested_by IS NULL "
        "AND source_deleted_at IS NULL) OR "
        "(source_delete_requested_at IS NOT NULL "
        "AND source_delete_requested_by IS NOT NULL "
        "AND (source_deleted_at IS NULL "
        "OR source_deleted_at >= source_delete_requested_at))",
    )
    # Exact Legacy-1 indexed rows predate governed decision records. Preserve
    # durable evidence that they have been served before deletion guards rely
    # on the approval actor/timestamp invariant.
    op.execute(
        """
        UPDATE document_versions
        SET legacy_approval_backfilled = (
              legacy_approval_backfilled
              OR approved_by IS NULL
              OR approved_at IS NULL
              OR decision_at IS NULL
            ),
            approved_by = COALESCE(approved_by, created_by),
            approved_at = COALESCE(approved_at, decision_at, updated_at, created_at),
            decision_at = COALESCE(decision_at, approved_at, updated_at, created_at)
        WHERE id = document_id
          AND sequence = 1
          AND version_label = 'Legacy 1'
          AND version_key = 'legacy 1'
          AND state = 'approved'
        """
    )
    op.create_check_constraint(
        "ck_document_versions_legacy_approval_backfill_scope",
        "document_versions",
        "NOT legacy_approval_backfilled OR "
        "(id = document_id AND sequence = 1 "
        "AND version_label = 'Legacy 1' AND version_key = 'legacy 1')",
    )
    op.create_check_constraint(
        "ck_document_versions_legacy_approval_evidence",
        "document_versions",
        "NOT (id = document_id AND sequence = 1 "
        "AND version_label = 'Legacy 1' AND version_key = 'legacy 1' "
        "AND state = 'approved') OR "
        "(approved_by IS NOT NULL AND approved_at IS NOT NULL "
        "AND decision_at IS NOT NULL)",
    )
    _install_deletion_guards()


def _restore_authority_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION openrag_validate_document_version_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.provenance_state='ready'
             OR OLD.state NOT IN ('draft','processing','rejected','failed') THEN
            RAISE EXCEPTION 'governed document version history cannot be deleted';
          END IF;
          RETURN OLD;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION openrag_protect_evidence_artifact() RETURNS trigger
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
            RETURN OLD;
          END IF;
          IF NOT FOUND OR owning_provenance='ready' OR NOT (
            owning_state='processing' OR owning_provenance='building'
          ) THEN
            RAISE EXCEPTION 'evidence artifact mutation requires processing/building owner';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE document_versions IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM document_versions "
            "WHERE source_delete_requested_at IS NOT NULL "
            "OR source_delete_requested_by IS NOT NULL "
            "OR source_deleted_at IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError(
            "source-deletion migration downgrade aborted: deletion history exists"
        )
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM document_versions "
            "WHERE legacy_approval_backfilled)"
        )
    ).scalar_one():
        raise RuntimeError(
            "source-deletion migration downgrade aborted: "
            "backfilled approval evidence exists"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_versions_source_deletion "
        "ON document_versions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_version_decision_insert_deletion_guard "
        "ON document_version_decision_records"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS openrag_validate_decision_insert_against_deletion()"
    )
    op.execute("DROP FUNCTION IF EXISTS openrag_validate_source_deletion_marker()")
    _restore_authority_guards()
    op.drop_constraint(
        "ck_document_versions_source_deletion_markers",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_legacy_approval_evidence",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_legacy_approval_backfill_scope",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "fk_document_versions_org_source_delete_requester",
        "document_versions",
        type_="foreignkey",
    )
    op.drop_column("document_versions", "source_deleted_at")
    op.drop_column("document_versions", "source_delete_requested_by")
    op.drop_column("document_versions", "source_delete_requested_at")
    op.drop_column("document_versions", "legacy_approval_backfilled")
