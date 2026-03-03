-- STEP 4: Entry timing — when does the qualified trader's FIRST trade happen
-- relative to market creation and resolution?
-- If insiders trade early (when prices are mispriced), there's alpha.
-- If they trade late (after price reflects outcome), there's no alpha.

SELECT
    timing_bucket,
    count(*) AS n_positions,
    round(countIf(correct = 1) / count(*), 3) AS hr,
    round(avg(realized_pnl), 2) AS avg_pnl,
    round(avg(entry_price), 3) AS avg_entry
FROM (
    SELECT
        p.condition_id,
        p.correct,
        p.realized_pnl,
        CASE WHEN p.position = 'YES' THEN p.avg_yes_price
             ELSE 1.0 - p.avg_yes_price END AS entry_price,
        dateDiff('hour', p.first_trade, p.resolved_at) AS hours_before_resolution,
        multiIf(
            dateDiff('hour', p.first_trade, p.resolved_at) <= 1, '<1h',
            dateDiff('hour', p.first_trade, p.resolved_at) <= 24, '1-24h',
            dateDiff('hour', p.first_trade, p.resolved_at) <= 168, '1-7d',
            dateDiff('hour', p.first_trade, p.resolved_at) <= 720, '7-30d',
            '30d+'
        ) AS timing_bucket
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
)
GROUP BY timing_bucket
ORDER BY timing_bucket
FORMAT PrettyCompactMonoBlock
