-- STEP 3: Does consensus improve prediction?
-- At the position level, how does HR change with number of unique qualified traders?
-- This tests whether "more skilled traders agree → more likely to be right"

SELECT
    n_qualified_traders,
    count(*) AS n_markets,
    round(countIf(yes_won = 1) / count(*), 3) AS yes_rate,
    round(avg(avg_entry), 3) AS avg_entry_price,
    round(median(days_to_resolve), 1) AS med_hold_days
FROM (
    SELECT
        p.condition_id,
        count(DISTINCT lower(p.trader)) AS n_qualified_traders,
        any(mr.token_won) AS yes_won,
        avg(p.avg_yes_price) AS avg_entry,
        median(dateDiff('day', p.first_trade, p.resolved_at)) AS days_to_resolve
    FROM trader_positions_resolved p
    INNER JOIN (
        SELECT DISTINCT condition_id, token_won
        FROM markets_resolved WHERE outcome = 'YES' AND resolved_at IS NOT NULL
    ) mr ON p.condition_id = mr.condition_id
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
    GROUP BY p.condition_id
)
GROUP BY n_qualified_traders
ORDER BY n_qualified_traders
FORMAT PrettyCompactMonoBlock
