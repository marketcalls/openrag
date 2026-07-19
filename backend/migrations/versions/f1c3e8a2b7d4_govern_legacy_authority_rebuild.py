"""govern legacy authority rebuild

Revision ID: f1c3e8a2b7d4
Revises: d8a4f2c91e37
Create Date: 2026-07-20 10:00:00.000000
"""

# ruff: noqa: S608
# This revision constructs DDL only from the two static clauses in
# _install_evidence_guard; it never interpolates runtime or user input.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1c3e8a2b7d4"
down_revision: str | Sequence[str] | None = "d8a4f2c91e37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXACT_LEGACY = (
    "sequence = 1 AND version_label = 'Legacy 1' AND version_key = 'legacy 1' "
    "AND parser_profile_version = 'legacy/parser-v1' "
    "AND ocr_profile_version = 'legacy/ocr-unknown-v1' "
    "AND chunking_profile_version = 'legacy/chunking-v1' "
    "AND embedding_profile_version = 'legacy/embedding-v1' "
    "AND index_profile_version = 'legacy/index-v1'"
)

_NON_LEGACY = (
    "version_label <> 'Legacy 1' AND version_key <> 'legacy 1' "
    "AND provenance_state <> 'legacy_pending' "
    "AND parser_profile_version <> 'legacy/parser-v1' "
    "AND ocr_profile_version <> 'legacy/ocr-unknown-v1' "
    "AND chunking_profile_version <> 'legacy/chunking-v1' "
    "AND embedding_profile_version <> 'legacy/embedding-v1' "
    "AND index_profile_version <> 'legacy/index-v1'"
)


def _install_evidence_guard(*, allow_legacy_rebuild: bool) -> None:
    legacy_clause = (
        " OR (owning_state='approved' AND owning_provenance='building' "
        "AND owning_is_exact_legacy)"
        if allow_legacy_rebuild
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION openrag_protect_evidence_artifact() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          owning_org uuid;
          owning_version uuid;
          owning_state text;
          owning_provenance text;
          deletion_requested_at timestamp;
          owning_is_exact_legacy boolean;
        BEGIN
          IF TG_OP='UPDATE' THEN
            RAISE EXCEPTION 'immutable evidence artifact cannot be updated';
          END IF;
          owning_org := CASE WHEN TG_OP='DELETE' THEN OLD.org_id ELSE NEW.org_id END;
          owning_version := CASE WHEN TG_OP='DELETE' THEN OLD.document_version_id
                                 ELSE NEW.document_version_id END;
          SELECT state, provenance_state, source_delete_requested_at,
                 (id = document_id AND sequence = 1
                  AND version_label = 'Legacy 1' AND version_key = 'legacy 1')
          INTO owning_state, owning_provenance, deletion_requested_at,
               owning_is_exact_legacy
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
          IF NOT (
            (owning_state='processing' AND owning_provenance='building')
            {legacy_clause}
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
    op.drop_constraint(
        "ck_document_versions_exact_legacy_contract",
        "document_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_document_versions_exact_legacy_contract",
        "document_versions",
        f"({_EXACT_LEGACY} AND ((state = 'approved' AND provenance_state IN "
        "('legacy_pending','building','ready','failed')) "
        "OR (state = 'failed' AND provenance_state = 'none') "
        "OR (state = 'processing' AND provenance_state = 'none'))) OR "
        f"({_NON_LEGACY})",
    )
    _install_evidence_guard(allow_legacy_rebuild=True)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE document_versions IN ACCESS EXCLUSIVE MODE"))
    governed_rebuild_exists = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM document_versions AS version WHERE "
            "version.id = version.document_id AND version.sequence = 1 "
            "AND version.version_label = 'Legacy 1' "
            "AND version.version_key = 'legacy 1' "
            "AND ((version.state = 'approved' "
            "AND version.provenance_state <> 'legacy_pending') "
            "OR EXISTS (SELECT 1 FROM document_blocks AS block "
            "WHERE block.document_version_id = version.id) "
            "OR EXISTS (SELECT 1 FROM document_chunks AS chunk "
            "WHERE chunk.document_version_id = version.id) "
            "OR EXISTS (SELECT 1 FROM document_evidence_spans AS span "
            "WHERE span.document_version_id = version.id)))"
        )
    )
    if governed_rebuild_exists:
        raise RuntimeError(
            "legacy authority rebuild downgrade aborted: governed rebuild state exists"
        )

    _install_evidence_guard(allow_legacy_rebuild=False)
    op.drop_constraint(
        "ck_document_versions_exact_legacy_contract",
        "document_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_document_versions_exact_legacy_contract",
        "document_versions",
        f"({_EXACT_LEGACY} AND ((state = 'approved' "
        "AND provenance_state = 'legacy_pending') "
        "OR (state = 'failed' AND provenance_state = 'none') "
        "OR (state = 'processing' AND provenance_state = 'none'))) OR "
        f"({_NON_LEGACY})",
    )
