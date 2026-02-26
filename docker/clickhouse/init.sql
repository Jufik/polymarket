CREATE TABLE IF NOT EXISTS polymarket.trades_raw (
    trade_id String,

    -- Market
    condition_id LowCardinality(String),
    asset_id String,

    -- Trade
    side Enum8('BUY' = 1, 'SELL' = 2),
    price Float32 CODEC(Gorilla, LZ4),
    size Float32,
    amount_usd Float32,
    fee_usd Float32,

    -- Participants
    maker Nullable(String),
    taker Nullable(String),

    -- Timing
    timestamp DateTime64(3) CODEC(DoubleDelta, LZ4),

    -- Provenance
    source LowCardinality(String),
    tx_hash Nullable(String),
    order_hash Nullable(String),
    block_number Nullable(UInt64),
    is_backfill Bool,

    -- ReplacingMergeTree version: on-chain (2) > off-chain (1)
    _version UInt8,

    -- Ingestion
    ingested_at DateTime64(3) DEFAULT now64(),
    published_at Float64 DEFAULT 0
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(timestamp)
ORDER BY (condition_id, timestamp, trade_id)
SETTINGS index_granularity = 8192;

-- Bloom filters for point lookups
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_maker maker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_taker taker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_trade_id trade_id TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_tx_hash tx_hash TYPE bloom_filter(0.01) GRANULARITY 1;

-- asset_id bloom filter for JOIN trades_raw ON asset_id (token_market_map lookups)
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_asset_id asset_id TYPE bloom_filter(0.01) GRANULARITY 4;

-- Timestamp minmax for time-range partition pruning
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_ts_minmax timestamp TYPE minmax GRANULARITY 8;

-- Convenience view (add FINAL to specific queries when exact dedup is needed)
-- e.g. SELECT * FROM polymarket.trades FINAL WHERE condition_id = '0x...'
CREATE VIEW IF NOT EXISTS polymarket.trades AS SELECT * FROM polymarket.trades_raw;

-- ---------------------------------------------------------------------------
-- Event & market metadata (reads directly from PostgreSQL — single source of truth)
-- ---------------------------------------------------------------------------
-- DateTime64(3) misreads PG TIMESTAMPTZ (year-2299 bug); use DateTime instead.
-- Loses sub-second precision but values are correct.
CREATE TABLE IF NOT EXISTS polymarket.events (
    id Int32,
    slug String,
    title String,
    category String,
    neg_risk Bool,
    active Bool,
    closed Bool,
    archived Bool,
    liquidity Float64,
    volume Float64,
    start_date Nullable(DateTime('Etc/UTC')),
    end_date Nullable(DateTime('Etc/UTC')),
    created_at Nullable(DateTime('Etc/UTC')),
    updated_at Nullable(DateTime('Etc/UTC'))
)
ENGINE = PostgreSQL('postgres:5432', 'polymarket', 'events', 'polymarket', 'polymarket');

CREATE TABLE IF NOT EXISTS polymarket.tags (
    id Int32,
    label String,
    slug String
)
ENGINE = PostgreSQL('postgres:5432', 'polymarket', 'tags', 'polymarket', 'polymarket');

CREATE TABLE IF NOT EXISTS polymarket.event_tags (
    event_id Int32,
    tag_id Int32
)
ENGINE = PostgreSQL('postgres:5432', 'polymarket', 'event_tags', 'polymarket', 'polymarket');

CREATE TABLE IF NOT EXISTS polymarket.markets (
    condition_id String,
    event_id Nullable(Int32),
    question String,
    slug String,
    category String,
    token_yes String,
    token_no String,
    neg_risk Bool,
    status String,
    resolution_value Int16,
    winner_outcome String,
    created_at Nullable(DateTime('Etc/UTC')),
    closed_at Nullable(DateTime('Etc/UTC')),
    resolved_at Nullable(DateTime('Etc/UTC')),
    updated_at Nullable(DateTime('Etc/UTC'))
)
ENGINE = PostgreSQL('postgres:5432', 'polymarket', 'markets', 'polymarket', 'polymarket');

-- Token -> Market lookup
CREATE TABLE IF NOT EXISTS polymarket.token_market_map (
    asset_id String,
    condition_id String,
    outcome String,
    winner Bool
)
ENGINE = PostgreSQL('postgres:5432', 'polymarket', 'token_market_map', 'polymarket', 'polymarket');
