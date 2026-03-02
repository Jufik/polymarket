-- S2 qualified traders with walk-forward training window.
-- Usage: create temp table for a specific training period, then join for OOS testing.
-- Parameters: {train_start}, {train_end}, {min_positions}, {min_excess_hr}
-- Result: trader, direction, wins, total, train_hr, train_excess_hr
-- Note: uses direction-specific base rates: YES=0.3628, NO=0.6372 (as of 2026-03)

CREATE TABLE _tmp_s2_qualified_{period_label} ENGINE = Memory AS
SELECT
    lower(p.trader) AS trader,
    p.position AS direction,
    countIf(p.correct = 1) AS wins,
    count(*) AS total,
    countIf(p.correct = 1) / count(*) AS train_hr,
    countIf(p.correct = 1) / count(*) -
        if(p.position = 'YES', 0.3628, 0.6372) AS train_excess_hr
FROM (
    SELECT * FROM trader_positions_resolved
    WHERE position IN ('YES', 'NO')
      AND toDate(resolved_at) >= '{train_start}'
      AND toDate(resolved_at) < '{train_end}'
) AS p
INNER JOIN markets AS m ON p.condition_id = m.condition_id
WHERE m.question NOT LIKE '%Up or Down%'
  AND m.question NOT LIKE '%up or down%'
GROUP BY trader, p.position
HAVING count(*) >= {min_positions}
   AND train_excess_hr >= {min_excess_hr}
