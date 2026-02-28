-- 005_trader_positions.sql
-- Proper trader market positions replacing ad-hoc _tmp_s1_positions / _tmp_s1_enriched.
--
-- Architecture:
--   trader_trade_agg (existing, per asset_id)
--       │
--       └── trader_market_positions_mv (chained MV, joins token_market_map)
--               │
--               └── trader_market_positions (SummingMergeTree, per condition_id)
--                       │
--                       └── trader_positions_resolved (VIEW, joins resolution)

-- =================================================================
-- 1. Target table — SummingMergeTree keyed by (trader, condition_id)
-- =================================================================
CREATE TABLE IF NOT EXISTS polymarket.trader_market_positions (
    trader          String,
    condition_id    LowCardinality(String),
    net_yes         Float64,
    net_no          Float64,
    volume          Float64,
    trade_count     UInt64,
    yes_px_vol      Float64,
    first_trade     SimpleAggregateFunction(min, DateTime64(3)),
    last_trade      SimpleAggregateFunction(max, DateTime64(3))
) ENGINE = SummingMergeTree(
    (net_yes, net_no, volume, trade_count, yes_px_vol)
)
ORDER BY (trader, condition_id);

-- =================================================================
-- 2. Chained MV — fires on each insert to trader_trade_agg,
--    pivots from per-asset to per-condition with YES/NO columns.
-- =================================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_market_positions_mv
TO polymarket.trader_market_positions AS
SELECT
    a.trader,
    a.condition_id,
    if(tm.outcome = 'YES', a.net_tokens, 0)                          AS net_yes,
    if(tm.outcome = 'NO',  a.net_tokens, 0)                          AS net_no,
    a.volume,
    a.trade_count,
    if(tm.outcome = 'YES', a.price_x_vol, a.volume - a.price_x_vol)  AS yes_px_vol,
    a.first_trade,
    a.last_trade
FROM polymarket.trader_trade_agg a
INNER JOIN polymarket.token_market_map tm ON a.asset_id = tm.asset_id;

-- =================================================================
-- 3. One-time backfill from existing trader_trade_agg data.
--    After this, the chained MV keeps everything current.
-- =================================================================
INSERT INTO polymarket.trader_market_positions
SELECT
    a.trader,
    a.condition_id,
    sumIf(a.net_tokens, tm.outcome = 'YES')                                      AS net_yes,
    sumIf(a.net_tokens, tm.outcome = 'NO')                                       AS net_no,
    sum(a.volume)                                                                AS volume,
    sum(a.trade_count)                                                           AS trade_count,
    sum(if(tm.outcome = 'YES', a.price_x_vol, a.volume - a.price_x_vol))         AS yes_px_vol,
    min(a.first_trade)                                                           AS first_trade,
    max(a.last_trade)                                                            AS last_trade
FROM (SELECT * FROM polymarket.trader_trade_agg FINAL) a
INNER JOIN polymarket.token_market_map tm ON a.asset_id = tm.asset_id
GROUP BY a.trader, a.condition_id;

-- =================================================================
-- 4. Resolved positions VIEW — classification + resolution outcome.
--    Replaces _tmp_s1_enriched. Always reflects latest resolution.
-- =================================================================
CREATE OR REPLACE VIEW polymarket.trader_positions_resolved AS
SELECT
    p.trader,
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN 'YES'
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 'NO'
        WHEN p.net_yes > 0.01 AND p.net_no > 0.01  THEN 'HEDGED'
        ELSE 'CLOSED'
    END AS position,
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN wavg_yes
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 1.0 - wavg_yes
        WHEN p.net_yes >= p.net_no                  THEN wavg_yes
        ELSE 1.0 - wavg_yes
    END AS dir_entry,
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN mr.yes_won
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN NOT mr.yes_won
        WHEN p.net_yes >= p.net_no                  THEN mr.yes_won
        ELSE NOT mr.yes_won
    END AS correct,
    p.volume AS market_volume,
    p.trade_count,
    mr.resolved_at,
    formatDateTime(mr.resolved_at, '%Y-%m') AS month
FROM (
    SELECT trader, condition_id, net_yes, net_no, volume, trade_count,
           yes_px_vol / nullIf(volume, 0) AS wavg_yes
    FROM polymarket.trader_market_positions FINAL
) p
INNER JOIN (
    SELECT condition_id, resolved_at,
           coalesce(token_won, false) AS yes_won
    FROM polymarket.markets_resolved
    WHERE outcome = 'YES'
) mr ON p.condition_id = mr.condition_id
WHERE NOT (p.net_yes <= 0.01 AND p.net_no <= 0.01);
