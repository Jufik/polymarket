-- STEP 5: Multi-trade dilution
-- In tick-by-tick, each trade from a qualified trader is a potential signal.
-- But a trader might make 10 trades in a market they get WRONG, generating
-- 10 "wrong" signals for 1 position. How concentrated are trades per position?

SELECT
    trades_bucket,
    count(*) AS n_positions,
    sum(n_trades) AS total_trades,
    round(countIf(correct = 1) / count(*), 3) AS position_hr,
    round(avg(realized_pnl), 2) AS avg_pnl
FROM (
    SELECT
        p.condition_id,
        lower(p.trader) AS trader,
        p.correct,
        p.realized_pnl,
        p.trade_count AS n_trades,
        multiIf(
            p.trade_count = 1, '1 trade',
            p.trade_count <= 3, '2-3 trades',
            p.trade_count <= 10, '4-10 trades',
            '10+ trades'
        ) AS trades_bucket
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
GROUP BY trades_bucket
ORDER BY trades_bucket
FORMAT PrettyCompactMonoBlock
