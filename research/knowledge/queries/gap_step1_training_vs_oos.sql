-- STEP 1: Training vs OOS performance of qualified traders
-- Do traders who were "skilled" in the training window stay skilled OOS?
-- Training: 6 months before 2025-07-01 (Jan-Jun 2025)
-- Test: July 2025

-- A) Training performance of the pool
SELECT
    'training' AS phase,
    count(DISTINCT trader) AS n_traders,
    count(*) AS n_positions,
    round(countIf(correct = 1) / count(*), 3) AS hr,
    round(avg(realized_pnl), 2) AS avg_pnl
FROM trader_positions_resolved
WHERE position IN ('YES', 'NO')
  AND toDate(resolved_at) >= '2025-01-01'
  AND toDate(resolved_at) < '2025-07-01'
  AND lower(trader) IN (
    SELECT lower(p.trader) FROM trader_positions_resolved p
    INNER JOIN markets AS m ON p.condition_id = m.condition_id
    WHERE p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '2025-01-01' AND toDate(p.resolved_at) < '2025-07-01'
      AND m.question NOT LIKE '%Up or Down%'
      AND p.condition_id NOT IN (SELECT condition_id FROM _tmp_excluded_sports_weather)
      AND (CASE WHEN p.position = 'YES' THEN p.avg_yes_price ELSE 1.0 - p.avg_yes_price END) < 0.95
    GROUP BY lower(p.trader), p.position
    HAVING count(*) >= 30
      AND (3.81 + countIf(p.correct = 1)) / (10.0 + count(*))
        - if(p.position = 'YES', 0.381, 0.619) >= 0.10
  )

UNION ALL

-- B) OOS performance of the SAME traders (July 2025)
SELECT
    'oos_july' AS phase,
    count(DISTINCT trader) AS n_traders,
    count(*) AS n_positions,
    round(countIf(correct = 1) / count(*), 3) AS hr,
    round(avg(realized_pnl), 2) AS avg_pnl
FROM trader_positions_resolved
WHERE position IN ('YES', 'NO')
  AND toDate(resolved_at) >= '2025-07-01'
  AND toDate(resolved_at) < '2025-08-01'
  AND lower(trader) IN (
    SELECT lower(p.trader) FROM trader_positions_resolved p
    INNER JOIN markets AS m ON p.condition_id = m.condition_id
    WHERE p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '2025-01-01' AND toDate(p.resolved_at) < '2025-07-01'
      AND m.question NOT LIKE '%Up or Down%'
      AND p.condition_id NOT IN (SELECT condition_id FROM _tmp_excluded_sports_weather)
      AND (CASE WHEN p.position = 'YES' THEN p.avg_yes_price ELSE 1.0 - p.avg_yes_price END) < 0.95
    GROUP BY lower(p.trader), p.position
    HAVING count(*) >= 30
      AND (3.81 + countIf(p.correct = 1)) / (10.0 + count(*))
        - if(p.position = 'YES', 0.381, 0.619) >= 0.10
  )
FORMAT PrettyCompactMonoBlock
