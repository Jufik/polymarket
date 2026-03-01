-- 006_fix_positions_pnl.sql
-- Fix trader_positions_resolved: replace direction-heuristic "correct" with
-- actual realized PnL at resolution.
--
-- Problem: the old CASE expression classified correctness by checking which
--          side (YES/NO) the trader held more of, ignoring entry price entirely.
--          A trader buying YES at 0.99 who "wins" is called correct despite
--          terrible risk/reward, and hedged positions were coin-flipped.
--
-- Fix:     Add net_usd (signed cash flow) to trader_market_positions, then
--          compute realized_pnl = payout + net_usd at resolution.
--          correct = realized_pnl > 0.
--
-- Requires: trader_trade_agg (003), token_market_map (PG engine)

-- =================================================================
-- 1. Drop the chained MV (safe — only captures new inserts)
-- =================================================================
DROP VIEW IF EXISTS polymarket.trader_market_positions_mv;

-- =================================================================
-- 2. Drop and recreate table with net_usd in summing set
--    (ALTER TABLE cannot add columns to SummingMergeTree's explicit
--     summing tuple — must recreate.)
-- =================================================================
DROP TABLE IF EXISTS polymarket.trader_market_positions;

CREATE TABLE IF NOT EXISTS polymarket.trader_market_positions (
    trader          String,
    condition_id    LowCardinality(String),
    net_yes         Float64,
    net_no          Float64,
    net_usd         Float64,
    volume          Float64,
    trade_count     UInt64,
    yes_px_vol      Float64,
    first_trade     SimpleAggregateFunction(min, DateTime64(3)),
    last_trade      SimpleAggregateFunction(max, DateTime64(3))
) ENGINE = SummingMergeTree(
    (net_yes, net_no, net_usd, volume, trade_count, yes_px_vol)
)
ORDER BY (trader, condition_id);

-- =================================================================
-- 3. Recreate MV with net_usd pass-through
-- =================================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_market_positions_mv
TO polymarket.trader_market_positions AS
SELECT
    a.trader,
    a.condition_id,
    if(tm.outcome = 'YES', a.net_tokens, 0)                          AS net_yes,
    if(tm.outcome = 'NO',  a.net_tokens, 0)                          AS net_no,
    a.net_usd,
    a.volume,
    a.trade_count,
    if(tm.outcome = 'YES', a.price_x_vol, a.volume - a.price_x_vol)  AS yes_px_vol,
    a.first_trade,
    a.last_trade
FROM polymarket.trader_trade_agg a
INNER JOIN polymarket.token_market_map tm ON a.asset_id = tm.asset_id;

-- =================================================================
-- 4. Backfill from existing trader_trade_agg data
-- =================================================================
INSERT INTO polymarket.trader_market_positions
SELECT
    a.trader,
    a.condition_id,
    sumIf(a.net_tokens, tm.outcome = 'YES')                                      AS net_yes,
    sumIf(a.net_tokens, tm.outcome = 'NO')                                       AS net_no,
    sum(a.net_usd)                                                               AS net_usd,
    sum(a.volume)                                                                AS volume,
    sum(a.trade_count)                                                           AS trade_count,
    sum(if(tm.outcome = 'YES', a.price_x_vol, a.volume - a.price_x_vol))         AS yes_px_vol,
    min(a.first_trade)                                                           AS first_trade,
    max(a.last_trade)                                                            AS last_trade
FROM (SELECT * FROM polymarket.trader_trade_agg FINAL) a
INNER JOIN polymarket.token_market_map tm ON a.asset_id = tm.asset_id
GROUP BY a.trader, a.condition_id;

-- =================================================================
-- 5. Fix trader_positions_resolved — PnL-based correctness
--
--    realized_pnl = payout + net_usd
--      payout: winning tokens × $1 (losers pay $0)
--      net_usd: signed cash flow (negative = net spent)
--    correct = realized_pnl > 0
-- =================================================================
CREATE OR REPLACE VIEW polymarket.trader_positions_resolved AS
SELECT
    p.trader,
    p.condition_id,
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN 'YES'
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 'NO'
        WHEN p.net_yes > 0.01 AND p.net_no > 0.01  THEN 'HEDGED'
        ELSE 'CLOSED'
    END AS position,
    wavg_yes AS avg_yes_price,
    -- Resolution payout: winning tokens pay $1 each, losers pay $0
    CASE WHEN mr.yes_won THEN p.net_yes ELSE p.net_no END AS payout,
    -- Realized PnL = payout + net_usd (net_usd negative = money spent)
    (CASE WHEN mr.yes_won THEN p.net_yes ELSE p.net_no END) + p.net_usd AS realized_pnl,
    -- Correct = actually profitable
    (CASE WHEN mr.yes_won THEN p.net_yes ELSE p.net_no END) + p.net_usd > 0 AS correct,
    p.net_usd,
    p.net_yes,
    p.net_no,
    p.volume AS market_volume,
    p.trade_count,
    mr.yes_won,
    mr.resolved_at,
    formatDateTime(mr.resolved_at, '%Y-%m') AS month
FROM (
    SELECT trader, condition_id, net_yes, net_no, net_usd, volume, trade_count,
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
