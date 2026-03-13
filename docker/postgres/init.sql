-- Event registry (parent of markets)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    slug TEXT,
    title TEXT,
    category TEXT,
    neg_risk BOOLEAN DEFAULT false,
    active BOOLEAN DEFAULT true,
    closed BOOLEAN DEFAULT false,
    archived BOOLEAN DEFAULT false,
    liquidity DOUBLE PRECISION DEFAULT 0,
    volume DOUBLE PRECISION DEFAULT 0,
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Market registry
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    event_id INTEGER REFERENCES events(id),
    question TEXT,
    slug TEXT,
    category TEXT,
    token_yes TEXT,
    token_no TEXT,
    neg_risk BOOLEAN DEFAULT false,
    status TEXT NOT NULL DEFAULT 'unknown',
    resolution_value SMALLINT DEFAULT 0,
    winner_outcome TEXT DEFAULT '',
    created_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    description TEXT DEFAULT '',
    resolution_source TEXT DEFAULT '',
    outcomes TEXT DEFAULT '',
    market_volume DOUBLE PRECISION DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tags
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    label TEXT,
    slug TEXT
);

-- Event <-> Tag many-to-many
CREATE TABLE IF NOT EXISTS event_tags (
    event_id INTEGER NOT NULL REFERENCES events(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (event_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_event_tags_tag ON event_tags(tag_id);

-- Token -> Market lookup (critical for normalizers)
CREATE TABLE IF NOT EXISTS token_market_map (
    asset_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL REFERENCES markets(condition_id),
    outcome TEXT NOT NULL,  -- 'YES' or 'NO'
    winner BOOLEAN DEFAULT false
);

-- Backfill progress tracking
CREATE TABLE IF NOT EXISTS backfill_progress (
    file_name VARCHAR(200) PRIMARY KEY,
    rows_total INTEGER NOT NULL,
    rows_normalized INTEGER NOT NULL,
    rows_dropped INTEGER NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Pipeline health
CREATE TABLE IF NOT EXISTS pipeline_health (
    id SERIAL PRIMARY KEY,
    source VARCHAR(30) NOT NULL,
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    last_event_at TIMESTAMPTZ,
    events_last_hour INTEGER,
    gap_seconds INTEGER,
    status VARCHAR(20)
);

-- Recovery job tracking (pm-recover cursor persistence)
CREATE TABLE IF NOT EXISTS recovery_jobs (
    id SERIAL PRIMARY KEY,
    from_ts BIGINT NOT NULL,
    cursor_ts BIGINT NOT NULL,
    target_ts BIGINT NOT NULL,
    total_published INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recovery_jobs_status ON recovery_jobs(status);

-- Strategy intents (captured from strategy.intents Kafka topic)
CREATE TABLE IF NOT EXISTS strategy_intents (
    id SERIAL PRIMARY KEY,
    strategy TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    size_usd DOUBLE PRECISION NOT NULL,
    urgency TEXT NOT NULL DEFAULT 'patient',
    max_price DOUBLE PRECISION,
    reason TEXT NOT NULL DEFAULT '',
    signal_time TIMESTAMPTZ NOT NULL,
    asset_id TEXT,
    disposition TEXT NOT NULL,
    rejection_reason TEXT NOT NULL DEFAULT '',
    filled_price DOUBLE PRECISION,
    filled_size_usd DOUBLE PRECISION,
    fee_usd DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}',
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_intents_strategy ON strategy_intents(strategy);
CREATE INDEX IF NOT EXISTS idx_intents_captured ON strategy_intents(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_intents_disposition ON strategy_intents(disposition);

-- Strategy copy pool (refreshed atomically by pm-strategy)
CREATE TABLE IF NOT EXISTS strategy_pool (
    strategy TEXT NOT NULL,
    trader_address TEXT NOT NULL,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (strategy, trader_address)
);

CREATE INDEX IF NOT EXISTS idx_markets_event_id ON markets(event_id);
CREATE INDEX IF NOT EXISTS idx_token_map_condition ON token_market_map(condition_id);
CREATE INDEX IF NOT EXISTS idx_health_source ON pipeline_health(source, checked_at);
