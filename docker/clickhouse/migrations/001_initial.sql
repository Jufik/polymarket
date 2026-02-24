-- Initial trades_raw table (already exists via init.sql, this is for documentation)
-- The actual init.sql creates the table on first startup.
-- This migration is a no-op for existing deployments.

CREATE TABLE IF NOT EXISTS polymarket.trades_raw (
    trade_id String,
    condition_id String,
    asset_id String,
    maker String DEFAULT '',
    taker String DEFAULT '',
    side String,
    outcome String,
    price Float64,
    amount Float64,
    fee_amount Float64,
    timestamp DateTime64(3),
    transaction_hash String DEFAULT '',
    order_hash String DEFAULT '',
    bucket_index UInt32 DEFAULT 0,
    source String DEFAULT 'parquet',
    _version UInt64 DEFAULT 1,
    ingested_at DateTime64(3) DEFAULT now64(),
    published_at Float64 DEFAULT 0
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (condition_id, timestamp, trade_id);
