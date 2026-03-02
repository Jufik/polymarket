-- research/knowledge/queries/insider_pool.sql
-- Compute insider scores for all traders on susceptible markets.
-- Parameters: {lookback_months}, {min_positions}
--
-- Stage 1: Classify markets by susceptibility
-- Stage 2: Compute 6-feature score per trader

WITH susceptible_markets AS (
    SELECT
        m.condition_id,
        multiIf(
            m.question LIKE '%Up or Down%'
                OR m.question LIKE '%up or down%'
                OR m.question LIKE '%coin flip%'
                OR m.question LIKE '%5-min%'
                OR m.question LIKE '%15-min%'
                OR m.question LIKE '%next 5 min%'
                OR m.question LIKE '%next 15 min%',
            'LOW',
            m.question LIKE '%SEC %'
                OR m.question LIKE '%FDA %'
                OR m.question LIKE '%regulat%'
                OR m.question LIKE '%approv%'
                OR m.question LIKE '%election%'
                OR m.question LIKE '%president%'
                OR m.question LIKE '%indict%'
                OR m.question LIKE '%verdict%',
            'HIGH',
            m.category IN ('Politics', 'Government', 'Legal', 'Regulatory'),
            'HIGH',
            m.category IN ('Sports', 'Entertainment', 'Esports'),
            'MEDIUM',
            'MEDIUM'
        ) AS susceptibility
    FROM markets AS m
),
-- Filter to only susceptible resolved markets
resolved_susceptible AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.volume AS market_volume,
        p.trade_count,
        p.avg_yes_price,
        p.resolved_at,
        p.yes_won,
        sm.susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= toDate(now()) - INTERVAL {lookback_months:UInt32} MONTH
),
-- Per-trader stats
trader_stats AS (
    SELECT
        trader,
        -- F1 inputs
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        count(*) AS total_positions,
        -- F2: bet conviction
        sum(market_volume) / count(*) AS avg_position_usd,
        -- F3: selectivity (markets per month)
        count(*) / greatest(
            dateDiff('month', min(resolved_at), max(resolved_at)) + 1, 1
        ) AS markets_per_month,
        -- F5: timing edge (avg realized PnL as proxy)
        avg(realized_pnl) AS avg_realized_pnl,
        -- F6: susceptibility concentration
        countIf(susceptibility = 'HIGH') / count(*) AS high_market_ratio,
        -- Extra stats
        sum(realized_pnl) AS total_pnl,
        avg(market_volume) AS avg_volume
    FROM resolved_susceptible
    GROUP BY trader
    HAVING count(*) >= {min_positions:UInt32}
),
-- F1: Bayesian hit rate
scored AS (
    SELECT
        *,
        -- YES posterior mean: (3.81 + yes_wins) / (10 + yes_total)
        (3.81 + yes_wins) / (10.0 + yes_total) AS bayesian_yes_hr,
        -- NO posterior mean: (6.19 + no_wins) / (10 + no_total)
        (6.19 + no_wins) / (10.0 + no_total) AS bayesian_no_hr,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total),
            (6.19 + no_wins) / (10.0 + no_total)
        ) AS effective_hr,
        if(
            (3.81 + yes_wins) / (10.0 + yes_total) >= (6.19 + no_wins) / (10.0 + no_total),
            'YES', 'NO'
        ) AS best_direction,
        -- F1: excess over base rate
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total) - 0.381,
            (6.19 + no_wins) / (10.0 + no_total) - 0.619
        ) AS hr_excess
    FROM trader_stats
)
SELECT
    trader,
    total_positions,
    yes_wins, yes_total,
    no_wins, no_total,
    effective_hr,
    best_direction,
    hr_excess,
    avg_position_usd,
    markets_per_month,
    avg_realized_pnl,
    high_market_ratio,
    total_pnl,
    avg_volume
FROM scored
ORDER BY hr_excess DESC, avg_position_usd DESC
LIMIT 500
