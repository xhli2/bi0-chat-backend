-- DEPRECATED: legacy SQL migration kept for historical reference only.
-- DO NOT execute this file in active environments.
-- Active migration system: Alembic Python revisions under /alembic/versions.

-- spliceai async jobs and archived results

CREATE TABLE IF NOT EXISTS spliceai_jobs (
    id VARCHAR(36) PRIMARY KEY,
    session_id VARCHAR(36) NULL REFERENCES chat_sessions(id) ON DELETE SET NULL,
    tenant_id VARCHAR(64) NOT NULL,
    user_id INTEGER NULL REFERENCES users(id),
    trace_id VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    variant_hgvs VARCHAR(255) NOT NULL,
    genome_build VARCHAR(16) NOT NULL DEFAULT 'GRCh38',
    gene_symbol VARCHAR(64) NULL,
    model_version VARCHAR(64) NOT NULL DEFAULT 'spliceai-mock-v1',
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    archived_result JSONB NULL,
    error_message TEXT NULL,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_spliceai_jobs_tenant_created ON spliceai_jobs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_spliceai_jobs_session ON spliceai_jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_spliceai_jobs_status ON spliceai_jobs(status);
