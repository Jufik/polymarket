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
SETTINGS index_granularity = 8192, deduplicate_merge_projection_mode = 'rebuild';

-- ---------------------------------------------------------------------------
-- Compression codecs: ZSTD(3) for high-cardinality strings, Delta for block_number
-- ---------------------------------------------------------------------------
ALTER TABLE polymarket.trades_raw MODIFY COLUMN trade_id String CODEC(ZSTD(3));
ALTER TABLE polymarket.trades_raw MODIFY COLUMN asset_id String CODEC(ZSTD(3));
ALTER TABLE polymarket.trades_raw MODIFY COLUMN tx_hash Nullable(String) CODEC(ZSTD(3));
ALTER TABLE polymarket.trades_raw MODIFY COLUMN order_hash Nullable(String) CODEC(ZSTD(3));
ALTER TABLE polymarket.trades_raw MODIFY COLUMN maker Nullable(String) CODEC(ZSTD(3));
ALTER TABLE polymarket.trades_raw MODIFY COLUMN taker Nullable(String) CODEC(ZSTD(3));
ALTER TABLE polymarket.trades_raw MODIFY COLUMN block_number Nullable(UInt64) CODEC(Delta, LZ4);

-- Bloom filters for point lookups
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_maker maker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_taker taker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_trade_id trade_id TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_tx_hash tx_hash TYPE bloom_filter(0.01) GRANULARITY 1;

-- asset_id bloom filter for JOIN trades_raw ON asset_id (token_market_map lookups)
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_asset_id asset_id TYPE bloom_filter(0.01) GRANULARITY 4;

-- Timestamp minmax for time-range partition pruning
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_ts_minmax timestamp TYPE minmax GRANULARITY 8;

-- ---------------------------------------------------------------------------
-- Projections: alternative sort orders for non-PK access patterns
-- ---------------------------------------------------------------------------
ALTER TABLE polymarket.trades_raw
  ADD PROJECTION IF NOT EXISTS proj_by_maker (
    SELECT * ORDER BY maker, timestamp
  );

ALTER TABLE polymarket.trades_raw
  ADD PROJECTION IF NOT EXISTS proj_by_source_time (
    SELECT * ORDER BY source, timestamp
  );

-- Convenience view (add FINAL to specific queries when exact dedup is needed)
-- e.g. SELECT * FROM polymarket.trades FINAL WHERE condition_id = '0x...'
CREATE VIEW IF NOT EXISTS polymarket.trades AS SELECT * FROM polymarket.trades_raw;

-- ---------------------------------------------------------------------------
-- Event & market metadata (views over pg_replicated — single source of truth)
-- ---------------------------------------------------------------------------
-- Production uses pg_replicated (MaterializedPostgreSQL engine) which provides
-- DateTime64(6) for TIMESTAMPTZ columns. These views alias into polymarket db.
-- Previous approach used direct PostgreSQL engine with DateTime (UInt32) but
-- that had precision loss and a hard ceiling at 2106-02-07.
CREATE VIEW IF NOT EXISTS polymarket.events AS SELECT * FROM pg_replicated.events;
CREATE VIEW IF NOT EXISTS polymarket.tags AS SELECT * FROM pg_replicated.tags;
CREATE VIEW IF NOT EXISTS polymarket.event_tags AS SELECT * FROM pg_replicated.event_tags;
CREATE VIEW IF NOT EXISTS polymarket.markets AS SELECT * FROM pg_replicated.markets;
CREATE VIEW IF NOT EXISTS polymarket.token_market_map AS SELECT * FROM pg_replicated.token_market_map;
