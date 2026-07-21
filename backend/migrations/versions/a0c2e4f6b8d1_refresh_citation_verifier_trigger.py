"""Refresh citation verifier trigger after measured model probes.

Revision ID: a0c2e4f6b8d1
Revises: f9b1d3e5a7c2
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a0c2e4f6b8d1"
down_revision: str | None = "f9b1d3e5a7c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UPGRADE = """
DO $migration$
DECLARE
  current_definition text;
  refreshed_definition text;
BEGIN
  SELECT pg_get_functiondef('openrag_validate_citation_write()'::regprocedure)
  INTO current_definition;

  refreshed_definition := replace(
    current_definition,
    'AND verifier.sync_status=''ready''',
    'AND verifier.probe_status=''passed'''
      || E'\n                AND verifier.supports_chat_completion'
      || E'\n                AND verifier.supports_streaming'
  );
  IF refreshed_definition = current_definition THEN
    RAISE EXCEPTION 'citation verifier trigger does not contain the legacy model predicate';
  END IF;
  EXECUTE refreshed_definition;
END
$migration$;
"""

_DOWNGRADE = """
DO $migration$
DECLARE
  current_definition text;
  legacy_definition text;
BEGIN
  SELECT pg_get_functiondef('openrag_validate_citation_write()'::regprocedure)
  INTO current_definition;

  legacy_definition := replace(
    current_definition,
    'AND verifier.probe_status=''passed'''
      || E'\n                AND verifier.supports_chat_completion'
      || E'\n                AND verifier.supports_streaming',
    'AND verifier.sync_status=''ready'''
  );
  IF legacy_definition = current_definition THEN
    RAISE EXCEPTION 'citation verifier trigger does not contain the measured probe predicate';
  END IF;
  EXECUTE legacy_definition;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(_UPGRADE)


def downgrade() -> None:
    op.execute(_DOWNGRADE)
