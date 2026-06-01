# Legacy SQL Migrations (Deprecated)

This directory is retained only for historical reference.

## Status

- Deprecated: do not add new SQL files here.
- Deprecated: do not run these SQL files in production workflows.

## Active Migration System

Use Python-based Alembic migrations instead:

- `uv run alembic upgrade head`
- `uv run alembic downgrade -1`
- `uv run alembic revision -m "your_migration_name"`

## Why kept

The original SQL scripts are preserved for auditability and migration history tracing.
