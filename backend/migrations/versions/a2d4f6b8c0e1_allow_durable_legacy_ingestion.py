"""allow durable legacy ingestion lifecycle

Revision ID: a2d4f6b8c0e1
Revises: f1c3e8a2b7d4
Create Date: 2026-07-20 11:00:00.000000
"""

# ruff: noqa: S608
# Constraint DDL is assembled exclusively from static migration constants.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2d4f6b8c0e1"
down_revision: str | Sequence[str] | None = "f1c3e8a2b7d4"
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
_OLD_EXACT_STATES = (
    "(state = 'approved' AND provenance_state IN "
    "('legacy_pending','building','ready','failed')) "
    "OR (state = 'failed' AND provenance_state = 'none') "
    "OR (state = 'processing' AND provenance_state = 'none')"
)
_DURABLE_EXACT_STATES = (
    "(state = 'approved' AND provenance_state IN "
    "('legacy_pending','building','ready','failed')) "
    "OR (state = 'failed' AND provenance_state IN ('none','failed')) "
    "OR (state = 'processing' AND provenance_state IN "
    "('none','building','failed')) "
    "OR (state = 'review' AND provenance_state = 'ready') "
    "OR (state IN ('rejected','superseded','obsolete') "
    "AND provenance_state = 'ready')"
)
_OLD_NULL_PAGE_STATES = (
    "(state = 'approved' AND provenance_state = 'legacy_pending') "
    "OR (state = 'failed' AND provenance_state = 'none') "
    "OR (state = 'processing' AND provenance_state = 'none')"
)
_DURABLE_NULL_PAGE_STATES = (
    "(state = 'approved' AND provenance_state = 'legacy_pending') "
    "OR (state = 'failed' AND provenance_state IN ('none','failed')) "
    "OR (state = 'processing' AND provenance_state IN "
    "('none','building','failed'))"
)


def _replace_constraints(*, exact_states: str, null_page_states: str) -> None:
    op.drop_constraint(
        "ck_document_versions_exact_legacy_contract",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_page_count_or_exact_legacy",
        "document_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_document_versions_exact_legacy_contract",
        "document_versions",
        f"({_EXACT_LEGACY} AND ({exact_states})) OR ({_NON_LEGACY})",
    )
    op.create_check_constraint(
        "ck_document_versions_page_count_or_exact_legacy",
        "document_versions",
        "source_page_count IS NOT NULL OR "
        "((version_label <> 'Legacy 1' AND version_key <> 'legacy 1') "
        "AND state IN ('draft','processing','failed') "
        "AND provenance_state <> 'ready') OR "
        "(sequence = 1 AND version_label = 'Legacy 1' "
        f"AND version_key = 'legacy 1' AND ({null_page_states}))",
    )


def upgrade() -> None:
    _replace_constraints(
        exact_states=_DURABLE_EXACT_STATES,
        null_page_states=_DURABLE_NULL_PAGE_STATES,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE document_versions IN ACCESS EXCLUSIVE MODE"))
    durable_state_exists = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM document_versions WHERE "
            "id = document_id AND sequence = 1 "
            "AND version_label = 'Legacy 1' AND version_key = 'legacy 1' AND ("
            "(state = 'processing' AND provenance_state <> 'none') "
            "OR (state = 'failed' AND provenance_state = 'failed') "
            "OR state IN ('review','rejected','superseded','obsolete')))"
        )
    )
    if durable_state_exists:
        raise RuntimeError(
            "durable legacy ingestion downgrade aborted: governed state exists"
        )
    _replace_constraints(
        exact_states=_OLD_EXACT_STATES,
        null_page_states=_OLD_NULL_PAGE_STATES,
    )
