-- STEP 7: The core gap — same market, position HR vs what a copier gets
-- For each market where qualified traders have positions in July 2025:
-- A) Did the qualified trader's NET POSITION win? (position-level = vectorized)
-- B) How many individual BUY trades did they make? (trade-level = tick-by-tick signals)
-- C) Each trade signals the SAME outcome, but the copier enters at THAT trade's price

SELECT
    position,
    count(*) AS n_positions,
    round(countIf(correct = 1) / count(*), 3) AS position_hr,
    round(avg(n_trades), 1) AS avg_trades_per_position,
    round(sum(n_trades), 0) AS total_trades,
    -- The copier sees total_trades signals but only n_positions unique outcomes
    round(sum(n_trades) / count(*), 1) AS signal_to_outcome_ratio,
    round(avg(realized_pnl), 2) AS avg_pnl_per_position,
    -- Copier's PnL is diluted by the ratio
    round(avg(realized_pnl) / avg(n_trades), 2) AS pnl_per_trade_signal
FROM (
    SELECT
        p.condition_id,
        lower(p.trader) AS trader,
        p.position,
        p.correct,
        p.realized_pnl,
        p.trade_count AS n_trades
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
GROUP BY position
FORMAT PrettyCompactMonoBlock
