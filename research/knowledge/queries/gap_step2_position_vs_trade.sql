-- STEP 2: Position-level HR vs individual-trade-level HR
-- The vectorized backtest uses POSITION-level outcomes (net position over many trades).
-- The tick-by-tick enters on INDIVIDUAL trades. How much does this differ?
--
-- For qualified traders in July 2025:
-- A) Position-level: each (trader, condition_id) is one observation — was the NET position correct?
-- B) Trade-level: each individual BUY trade — was the MARKET correct? (same answer per market,
--    but weighted by number of trades, not positions)

-- A) Position-level HR (what vectorized sees)
SELECT
    'position_level' AS level,
    p.position AS direction,
    count(*) AS n,
    round(countIf(p.correct = 1) / count(*), 3) AS hr,
    round(avg(p.realized_pnl), 2) AS avg_pnl
FROM trader_positions_resolved p
WHERE p.position IN ('YES', 'NO')
  AND toDate(p.resolved_at) >= '2025-07-01'
  AND toDate(p.resolved_at) < '2025-08-01'
  AND lower(p.trader) IN (
    SELECT lower(p2.trader) FROM trader_positions_resolved p2
    INNER JOIN markets AS m ON p2.condition_id = m.condition_id
    WHERE p2.position IN ('YES', 'NO')
      AND toDate(p2.resolved_at) >= '2025-01-01' AND toDate(p2.resolved_at) < '2025-07-01'
      AND m.question NOT LIKE '%Up or Down%'
      AND p2.condition_id NOT IN (SELECT condition_id FROM _tmp_excluded_sports_weather)
    GROUP BY lower(p2.trader), p2.position
    HAVING count(*) >= 30
      AND countIf(p2.correct = 1) / count(*) - if(p2.position = 'YES', 0.381, 0.619) >= 0.10
  )
  AND p.condition_id NOT IN (SELECT condition_id FROM _tmp_excluded_sports_weather)
GROUP BY p.position

UNION ALL

-- B) Trade-level HR: each individual BUY trade from qualified makers
-- The key difference: a trader might make 5 trades in one market,
-- and each trade "votes" for the same outcome. Position-level counts it once.
SELECT
    'trade_level' AS level,
    side AS direction,
    count(*) AS n,
    -- For trade-level: did the MARKET resolve in the BUY direction?
    -- We need to join with resolution data
    0 AS hr,
    0 AS avg_pnl
FROM trades_raw FINAL
WHERE side = 'BUY'
  AND toDate(timestamp) >= '2025-07-01'
  AND toDate(timestamp) < '2025-08-01'
  AND lower(maker) IN (
    SELECT lower(p2.trader) FROM trader_positions_resolved p2
    INNER JOIN markets AS m ON p2.condition_id = m.condition_id
    WHERE p2.position IN ('YES', 'NO')
      AND toDate(p2.resolved_at) >= '2025-01-01' AND toDate(p2.resolved_at) < '2025-07-01'
      AND m.question NOT LIKE '%Up or Down%'
      AND p2.condition_id NOT IN (SELECT condition_id FROM _tmp_excluded_sports_weather)
    GROUP BY lower(p2.trader), p2.position
    HAVING count(*) >= 30
      AND countIf(p2.correct = 1) / count(*) - if(p2.position = 'YES', 0.381, 0.619) >= 0.10
  )
GROUP BY side
FORMAT PrettyCompactMonoBlock
