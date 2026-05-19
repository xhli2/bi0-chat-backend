# Next Iteration TODO

- [ ] Add durable task metadata table in PostgreSQL (owner, created_by, queue latency, run latency, failure reason).
- [ ] Add per-user concurrency limits and global queue admission control (return 429 with retry hint).
- [x] Add Celery task retry policies, dead-letter queue, and poison-task handling.
- [x] Implement true `openai-agents` streaming bridge (stream partial tokens directly to Redis Stream).
- [x] Add model allowlist and tenant-level model policy enforcement.
- [ ] Add prompt template registry (`base`, `agent_type`, `tenant`, `request`) with versioning.
- [ ] Add conversation/session memory storage and truncation policy.
- [ ] Add OpenTelemetry tracing across API -> Celery -> Redis Stream.
- [x] Add admin endpoints for queue depth, worker health, and task diagnostics.
- [ ] Add integration tests using ephemeral Redis and Celery worker in CI.
