-- S2 Insider Copy: Load BUY trades from insider pool during test window.
-- These are the trades that trigger entry signals in tick-by-tick replay.
-- Parameters: {test_start}, {test_end}
-- The insider pool traders are passed via a temp table _tmp_s2_tick_pool.
--
-- Output: all trades in markets where at least one insider BUYs during test window,
--         ordered by timestamp (critical for tick-by-tick replay).

SELECT
    tr.trade_id,
    tr.condition_id,
    tr.asset_id,
    tr.side,
    tr.price,
    tr.size,
    tr.amount_usd,
    tr.fee_usd,
    lower(tr.maker) AS maker,
    lower(tr.taker) AS taker,
    tr.timestamp,
    tr.source,
    tr.tx_hash,
    tr.order_hash,
    tr.block_number,
    tr.is_backfill,
    tr._version,
    tr.published_at
FROM (SELECT * FROM trades_raw FINAL) AS tr
WHERE tr.condition_id IN (
    -- Markets with at least one insider BUY
    SELECT DISTINCT tr2.condition_id
    FROM (SELECT * FROM trades_raw FINAL) AS tr2
    WHERE tr2.side = 'BUY'
      AND lower(tr2.maker) IN (SELECT trader FROM _tmp_s2_tick_pool)
      AND tr2.timestamp >= '{test_start}'
      AND tr2.timestamp < '{test_end}'
)
AND tr.timestamp >= '{test_start}'
AND tr.timestamp < '{test_end}'
ORDER BY tr.timestamp
