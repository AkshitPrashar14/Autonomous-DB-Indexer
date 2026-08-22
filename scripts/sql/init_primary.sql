-- DBAutonomy — Primary Database Initialization
-- Run on first startup of db_primary container.

-- Enable pg_stat_statements for slow query detection
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- ============================================================
-- Decision Repository Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS optimization_records (
    record_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL,
    table_name      TEXT NOT NULL,
    candidate_json  JSONB NOT NULL,
    baseline_json   JSONB NOT NULL,
    experiment_json JSONB NOT NULL,
    reward          DOUBLE PRECISION NOT NULL,
    decision_json   JSONB NOT NULL,
    status          TEXT NOT NULL,  -- deployed|rejected|failed|skipped
    deployed_index  TEXT,
    context_vector  DOUBLE PRECISION[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_records_table_name ON optimization_records (table_name);
CREATE INDEX IF NOT EXISTS idx_records_created_at ON optimization_records (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_records_status ON optimization_records (status);

CREATE TABLE IF NOT EXISTS bandit_state (
    id          SERIAL PRIMARY KEY,
    state_json  JSONB NOT NULL,
    saved_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only keep one row; upsert pattern used by DecisionRepository
CREATE UNIQUE INDEX IF NOT EXISTS idx_bandit_state_singleton
    ON bandit_state ((1));

CREATE TABLE IF NOT EXISTS safety_decisions (
    id              SERIAL PRIMARY KEY,
    record_id       UUID REFERENCES optimization_records(record_id),
    approved        BOOLEAN NOT NULL,
    reason          TEXT NOT NULL,
    stages_passed   TEXT[],
    stages_failed   TEXT[],
    reward          DOUBLE PRECISION,
    risk_score      DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only: no UPDATE/DELETE allowed (enforced by application layer)
CREATE INDEX IF NOT EXISTS idx_safety_created_at ON safety_decisions (created_at DESC);

CREATE TABLE IF NOT EXISTS deployed_indexes (
    id              SERIAL PRIMARY KEY,
    record_id       UUID REFERENCES optimization_records(record_id),
    index_name      TEXT NOT NULL UNIQUE,
    table_name      TEXT NOT NULL,
    columns         TEXT[],
    index_type      TEXT,
    create_sql      TEXT NOT NULL,
    deployed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE
);

-- ============================================================
-- Demo Data Tables (populated by scripts/seed_demo_db.py)
-- ============================================================

CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL PRIMARY KEY,
    customer_id     BIGINT NOT NULL,
    product_id      BIGINT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    amount          NUMERIC(10,2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    price           NUMERIC(10,2) NOT NULL,
    stock           INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
