# backend-temp

FastAPI backend scaffold with staged capabilities:

- P0: health checks, versioned routing, unified errors, config, logging, container runtime
- P1: async DB access, JWT auth, CRUD, pagination/filtering, soft delete
- P2: SSE stream, reconnect with `Last-Event-ID`, task status broadcast, cancellation
- P3: extensible agent factory, unified event protocol (`status`/`delta`/`part`/`usage`/`tool_start`/`tool_end`/`tool_error`)
- Infra v1: Celery workers + priority queues (`high/default/low`) + Redis stream-backed resumable SSE
- Tools v1: ToolRegistry with schema/permission/timeout governance + tenant whitelist + function-calling

## Run locally

1. `uv sync` (user-managed environment)
2. Start Redis (local or docker)
3. Start API: `uv run uvicorn app.main:app --reload`
4. Start worker: `uv run celery -A app.worker.celery_app.celery_app worker -Q high,default,low --loglevel=info`
5. Apply DB migrations: `uv run alembic upgrade head`

## Run with containers

Image build uses a **multi-stage** flow: `uv sync --frozen` from `uv.lock` in the builder, then `python:3.13-slim` runtime with `.venv` copied in (same pattern for `api` and `worker`).

1. `docker compose up --build`
2. Open `http://localhost:8000/docs`

## Agent run API (model + priority)

- `POST /api/v1/agents/run`
- Request body:
  - `agent_type`: skill key, e.g. `echo`
  - `prompt`: input text
  - `model`: model selected by frontend (`builtin` by default)
  - `priority`: `high` | `default` | `low`
  - `session_id`: existing session id (optional; auto-created if omitted)
  - `context_policy`: `balanced` | `recent_first` | `summary_heavy`
- Optional headers:
  - `X-Tenant-ID`: tenant identifier used for model policy checks
  - `X-Trace-ID`: caller trace id for end-to-end correlation (auto-generated if omitted)
  - `X-User-ID`: optional user id used for session ownership

SSE reconnect uses `Last-Event-ID` on `GET /api/v1/tasks/{task_id}/stream`.

## Reliability and observability

- Celery retries with exponential backoff (`CELERY_TASK_MAX_RETRIES` + backoff settings).
- Exhausted retries are marked as poison tasks and moved to dead-letter list in Redis.
- Admin diagnostics:
  - `GET /api/v1/admin/queues`
  - `GET /api/v1/admin/workers`
  - `GET /api/v1/admin/tasks/{task_id}/diagnostics`
  - `GET /api/v1/admin/sessions/{session_id}/diagnostics`
  - `GET /api/v1/admin/dead-letter`
  - `GET /api/v1/admin/tools`
  - `GET /api/v1/admin/tenants/{tenant_id}/tools`

## ToolRegistry and Agent Tools

- Tool execution lifecycle emits:
  - `tool_start`
  - `tool_end`
  - `tool_error`
- Tenant governance:
  - `TENANT_TOOL_POLICIES_JSON` controls tool allowlist
  - agent_type binding controls tool exposure to model
- Runtime controls:
  - `TOOL_CALL_TIMEOUT_SECONDS_DEFAULT`
  - `TOOL_CALL_TIMEOUT_SECONDS_MAX`
  - `AGENT_TOOL_BINDINGS_JSON`

## Session history management (V1)

- Persistent storage in DB:
  - `chat_sessions`
  - `chat_messages`
  - `chat_summaries`
  - `chat_memory_kv`
- Session endpoints:
  - `POST /api/v1/sessions`
  - `GET /api/v1/sessions/{session_id}`
  - `GET /api/v1/sessions/{session_id}/messages`
  - `GET /api/v1/sessions/{session_id}/memory`
  - `GET /api/v1/sessions/{session_id}/summary`
  - `POST /api/v1/sessions/{session_id}/summarize`
  - `GET /api/v1/sessions/{session_id}/diagnostics`
- Context assembly layers:
  - system + tenant policy
  - session summary
  - KV memory
  - recent messages
  - current user message
- Hybrid budget policy and retention are configurable via `.env`.

## Database migrations (Python, PostgreSQL)

- Migration framework: **Alembic** (`alembic/` + `alembic.ini`)
- Apply all migrations:
  - `uv run alembic upgrade head`
- Roll back one revision:
  - `uv run alembic downgrade -1`
- Create a new Python migration file:
  - `uv run alembic revision -m "your_migration_name"`

Note:
- App startup no longer auto-creates schema in non-test environments.
- Legacy SQL scripts under `app/db/migrations/` are deprecated and retained only as historical references.

## Tests

- `uv run pytest`
