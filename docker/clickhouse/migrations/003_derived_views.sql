-- 003_derived_views.sql
-- Derived feature tables: markets_resolved VIEW, trader_volumes MV, trader_trade_agg MV

-- =================================================================
-- 0. Recreate PG views (pg_replicated provides DateTime64(6) for TIMESTAMPTZ)
-- =================================================================
DROP TABLE IF EXISTS polymarket.token_market_map;
CREATE VIEW IF NOT EXISTS polymarket.token_market_map AS SELECT * FROM pg_replicated.token_market_map;

DROP TABLE IF EXISTS polymarket.markets;
CREATE VIEW IF NOT EXISTS polymarket.markets AS SELECT * FROM pg_replicated.markets;

-- =================================================================
-- 1. markets_resolved — VIEW over PG engine tables (always fresh)
-- =================================================================
CREATE OR REPLACE VIEW polymarket.markets_resolved AS
SELECT
    m.condition_id,
    m.resolution_value,
    m.winner_outcome,
    m.resolved_at,
    tm.asset_id,
    tm.outcome,
    tm.winner AS token_won
FROM polymarket.markets m
INNER JOIN polymarket.token_market_map tm ON m.condition_id = tm.condition_id
WHERE m.resolution_value = 1;

-- =================================================================
-- 2. trader_volumes — SummingMergeTree for MVF computation
-- =================================================================
CREATE TABLE IF NOT EXISTS polymarket.trader_volumes (
    trader          String,
    maker_vol       Float64,
    taker_vol       Float64
) ENGINE = SummingMergeTree((maker_vol, taker_vol))
ORDER BY trader;

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_volumes_maker_mv
TO polymarket.trader_volumes AS
SELECT
    maker AS trader,
    amount_usd AS maker_vol,
    0 AS taker_vol
FROM polymarket.trades_raw
WHERE maker IS NOT NULL AND maker != '';

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_volumes_taker_mv
TO polymarket.trader_volumes AS
SELECT
    taker AS trader,
    0 AS maker_vol,
    amount_usd AS taker_vol
FROM polymarket.trades_raw
WHERE taker IS NOT NULL AND taker != '';

-- =================================================================
-- 3. trader_trade_agg — SummingMergeTree for per-market aggregates
--    Both maker and taker perspectives, matching derived.py logic.
-- =================================================================
CREATE TABLE IF NOT EXISTS polymarket.trader_trade_agg (
    trader          String,
    condition_id    LowCardinality(String),
    asset_id        String,
    -- Signed: maker BUY = +tokens/-usd, taker BUY = -tokens/+usd
    net_tokens      Float64,
    net_usd         Float64,
    total_fees      Float64,
    volume          Float64,
    trade_count     UInt64,
    first_trade     SimpleAggregateFunction(min, DateTime64(3)),
    last_trade      SimpleAggregateFunction(max, DateTime64(3)),
    price_x_vol     Float64
) ENGINE = SummingMergeTree(
    (net_tokens, net_usd, total_fees, volume, trade_count, price_x_vol)
)
ORDER BY (trader, condition_id, asset_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_trade_agg_maker_mv
TO polymarket.trader_trade_agg AS
SELECT
    maker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', toFloat64(size), -toFloat64(size)) AS net_tokens,
    if(side = 'BUY', -toFloat64(amount_usd), toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM polymarket.trades_raw
WHERE maker IS NOT NULL AND maker != '';

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_trade_agg_taker_mv
TO polymarket.trader_trade_agg AS
SELECT
    taker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', -toFloat64(size), toFloat64(size)) AS net_tokens,
    if(side = 'BUY', toFloat64(amount_usd), -toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM polymarket.trades_raw
WHERE taker IS NOT NULL AND taker != ''
