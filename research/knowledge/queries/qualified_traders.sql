-- Find traders with high hit rate on resolved directional positions.
-- Parameters: {lookback_months}, {min_positions}, {min_hr}
-- Usage: trader pool for copy-trading strategies.
-- Note: uses toDate(resolved_at) because `month` column is String type.

SELECT
    lower(p.trader) AS trader,
    countIf(p.correct = 1) AS wins,
    count(*) AS total,
    countIf(p.correct = 1) / count(*) AS hit_rate
FROM (
    SELECT * FROM trader_positions_resolved
    WHERE position IN ('YES', 'NO')
      AND toDate(resolved_at) >= toDate(now()) - INTERVAL {lookback_months} MONTH
) AS p
INNER JOIN markets AS m ON p.condition_id = m.condition_id
WHERE m.question NOT LIKE '%Up or Down%'
  AND m.question NOT LIKE '%up or down%'
GROUP BY trader
HAVING count(*) >= {min_positions}
   AND hit_rate >= {min_hr}
