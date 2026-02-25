-- 004_backfill_derived.sql
-- One-time backfill of derived tables from existing trades_raw data.
-- After this, MVs keep everything current automatically.

-- Backfill trader_volumes — maker perspective
INSERT INTO polymarket.trader_volumes
SELECT
    maker AS trader,
    sum(toFloat64(amount_usd)) AS maker_vol,
    0 AS taker_vol
FROM polymarket.trades_raw FINAL
WHERE maker IS NOT NULL AND maker != ''
GROUP BY maker;

-- Backfill trader_volumes — taker perspective
INSERT INTO polymarket.trader_volumes
SELECT
    taker AS trader,
    0 AS maker_vol,
    sum(toFloat64(amount_usd)) AS taker_vol
FROM polymarket.trades_raw FINAL
WHERE taker IS NOT NULL AND taker != ''
GROUP BY taker;

-- Backfill trader_trade_agg — maker perspective
INSERT INTO polymarket.trader_trade_agg
SELECT
    maker AS trader,
    condition_id,
    asset_id,
    sum(if(side = 'BUY', toFloat64(size), -toFloat64(size))) AS net_tokens,
    sum(if(side = 'BUY', -toFloat64(amount_usd), toFloat64(amount_usd))) AS net_usd,
    sum(toFloat64(fee_usd)) AS total_fees,
    sum(toFloat64(amount_usd)) AS volume,
    toUInt64(count()) AS trade_count,
    min(timestamp) AS first_trade,
    max(timestamp) AS last_trade,
    sum(toFloat64(price) * toFloat64(amount_usd)) AS price_x_vol
FROM polymarket.trades_raw FINAL
WHERE maker IS NOT NULL AND maker != ''
GROUP BY maker, condition_id, asset_id;

-- Backfill trader_trade_agg — taker perspective
INSERT INTO polymarket.trader_trade_agg
SELECT
    taker AS trader,
    condition_id,
    asset_id,
    sum(if(side = 'BUY', -toFloat64(size), toFloat64(size))) AS net_tokens,
    sum(if(side = 'BUY', toFloat64(amount_usd), -toFloat64(amount_usd))) AS net_usd,
    sum(toFloat64(fee_usd)) AS total_fees,
    sum(toFloat64(amount_usd)) AS volume,
    toUInt64(count()) AS trade_count,
    min(timestamp) AS first_trade,
    max(timestamp) AS last_trade,
    sum(toFloat64(price) * toFloat64(amount_usd)) AS price_x_vol
FROM polymarket.trades_raw FINAL
WHERE taker IS NOT NULL AND taker != ''
GROUP BY taker, condition_id, asset_id
