# ADR-0003: Naive-UTC Datetimes in Postgres

**Date:** 2026-07-18  
**Status:** Accepted

## Context

SQLAlchemy maps `Mapped[datetime]` to `TIMESTAMP WITHOUT TIME ZONE` by default, and
asyncpg rejects timezone-aware Python datetimes for such columns. Plan A shipped all
timestamp columns as naive; mid-phase migration of every column to
`DateTime(timezone=True)` would churn every table and risk a mixed-column state where
some comparisons silently misbehave. Alternatives considered: keep naive columns and
standardize on naive-UTC values; migrate all columns to timestamptz now; or choose per
column.

## Decision

All persisted datetimes are naive UTC:

- Writes go through `openrag.core.db.naive_utc()`—
  `datetime.now(UTC).replace(tzinfo=None)`—as the single write-path idiom. The
  `UUIDPk.created_at` default uses the same expression.
- Reads that must be compared against aware datetimes re-attach UTC with
  `value.replace(tzinfo=UTC)`, as the auth service already does for refresh-token
  expiry.

## Rationale

- Zero migration churn mid-phase; one convention everywhere avoids a mixed state.
- UTC-only storage keeps ordering and arithmetic correct; the timezone suffix carries
  no information when every value is UTC by construction.

## Consequences

- Comparing a database datetime with `datetime.now(UTC)` without normalizing raises
  `TypeError`—a loud failure that tests catch immediately.
- API responses serialize naive values; the OpenAPI contract documents all timestamps
  as UTC. Revisit with one Alembic migration to timestamptz if cross-timezone
  deployments ever read the database directly.
