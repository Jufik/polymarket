-- STEP 6: Direction mismatch — are qualified traders trading in their
-- qualified direction OOS, or do they switch?
-- A trader qualified as NO-skilled might buy YES tokens OOS.

SELECT
    qualified_dir,
    oos_dir,
    count(*) AS n_trades,
    round(countIf(correct = 1) / count(*), 3) AS hr,
    round(avg(realized_pnl), 2) AS avg_pnl
FROM (
    SELECT
        q.direction AS qualified_dir,
        p.position AS oos_dir,
        p.correct,
        p.realized_pnl
    FROM trader_positions_resolved p
    INNER JOIN (
        -- Qualified traders with their qualified direction
        SELECT lower(p2.trader) AS trader, p2.position AS direction
        FROM trader_positions_resolved p2
        INNER JOIN markets AS m ON p2.condition_id = m.condition_id
        WHERE p2.position IN ('YES', 'NO')
          AND toDate(p2.resolved_at) >= '2025-01-01' AND toDate(p2.resolved_at) < '2025-07-01'
          AND m.question NOT LIKE '%Up or Down%'
          AND p2.condition_id NOT IN (SELECT condition_id FROM _tmp_excluded_sports_weather)
          AND (CASE WHEN p2.position = 'YES' THEN p2.avg_yes_price ELSE 1.0 - p2.avg_yes_price END) < 0.95
        GROUP BY lower(p2.trader), p2.position
        HAVING count(*) >= 30
          AND (3.81 + countIf(p2.correct = 1)) / (10.0 + count(*))
            - if(p2.position = 'YES', 0.381, 0.619) >= 0.10
    ) q ON lower(p.trader) = q.trader
    WHERE p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '2025-07-01'
      AND toDate(p.resolved_at) < '2025-08-01'
      AND p.condition_id NOT IN (SELECT condition_id FROM _tmp_excluded_sports_weather)
)
GROUP BY qualified_dir, oos_dir
ORDER BY qualified_dir, oos_dir
FORMAT PrettyCompactMonoBlock
